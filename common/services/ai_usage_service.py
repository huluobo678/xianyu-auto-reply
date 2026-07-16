from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from common.models.ai_usage import AIAccountMonthlyUsage, AIAccountQuotaConfig
from common.models.ai_usage import AIQuotaConfig, AIUsageRequest, AIUserMonthlyUsage
from common.models.xy_account import XYAccount
from common.utils.time_utils import get_beijing_now_naive

DEFAULT_ACCOUNT_RPM = 60
DEFAULT_ACCOUNT_CONCURRENCY = 1
INPUT_COST_PER_MILLION = Decimal(10)
OUTPUT_COST_PER_MILLION = Decimal(30)


class AIQuotaExceededError(RuntimeError):
    pass


class AIAccountRateLimitError(RuntimeError):
    pass


class AIAccountConcurrencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIReservation:
    request_id: int
    idempotency_key: str
    duplicate: bool = False


def current_period_start(now: datetime | None = None) -> date:
    current = now or get_beijing_now_naive()
    return date(current.year, current.month, 1)


def estimate_tokens(text_value: str | None) -> int:
    return max(1, math.ceil(len(text_value or "") * 2))


def estimate_cost(input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) * INPUT_COST_PER_MILLION
        + Decimal(output_tokens) * OUTPUT_COST_PER_MILLION
    ) / Decimal(1_000_000)


class AIUsageService:
    @staticmethod
    async def reserve(session: AsyncSession, *, account_id: str, idempotency_key: str, source_message_id: str | None, chat_id: str) -> AIReservation:
        now = get_beijing_now_naive()
        period = current_period_start(now)
        return await AIUsageService._reserve_transaction(session, account_id, idempotency_key, source_message_id, chat_id, now, period)

    @staticmethod
    async def _reserve_transaction(session, account_id, key, source_id, chat_id, now, period):
        async with session.begin():
            request = await session.scalar(select(AIUsageRequest).where(AIUsageRequest.idempotency_key == key).with_for_update())
            if request:
                return AIReservation(request.id, key, True)
            account = await session.scalar(select(XYAccount).where(XYAccount.account_id == account_id).with_for_update())
            if not account:
                raise ValueError(f"AI account not found: {account_id}")
            request = await session.scalar(
                select(AIUsageRequest).where(AIUsageRequest.idempotency_key == key).with_for_update()
            )
            if request:
                return AIReservation(request.id, key, True)
            await AIUsageService._ensure_usage_rows(session, account, period)
            return await AIUsageService._reserve_locked(session, account, period, key, source_id, chat_id, now)

    @staticmethod
    async def _reserve_locked(session, account, period, key, source_id, chat_id, now):
        user_usage = await session.scalar(
            select(AIUserMonthlyUsage).where(
                AIUserMonthlyUsage.user_id == account.owner_id,
                AIUserMonthlyUsage.period_start == period,
            ).with_for_update()
        )
        account_usage = await session.scalar(
            select(AIAccountMonthlyUsage).where(
                AIAccountMonthlyUsage.account_pk == account.id,
                AIAccountMonthlyUsage.period_start == period,
            ).with_for_update()
        )
        user_config = await session.scalar(select(AIQuotaConfig).where(AIQuotaConfig.user_id == account.owner_id))
        account_config = await session.scalar(select(AIAccountQuotaConfig).where(AIAccountQuotaConfig.account_pk == account.id))
        AIUsageService._check_quota(user_usage, AIUsageService._get_user_quota(user_config), "user")
        AIUsageService._check_quota(account_usage, account_config.monthly_quota if account_config else None, "account")
        concurrency = account_config.max_concurrency if account_config else DEFAULT_ACCOUNT_CONCURRENCY
        if account_usage.reserved_replies >= max(1, concurrency):
            raise AIAccountConcurrencyError("AI account concurrency limit reached")
        rpm = account_config.requests_per_minute if account_config else DEFAULT_ACCOUNT_RPM
        recent_count = await session.scalar(
            select(func.count(AIUsageRequest.id)).where(
                AIUsageRequest.account_pk == account.id,
                AIUsageRequest.created_at >= now - timedelta(minutes=1),
                AIUsageRequest.status.in_(("reserved", "committed")),
            )
        )
        if int(recent_count or 0) >= max(1, rpm):
            raise AIAccountRateLimitError("AI account requests-per-minute limit reached")
        request = AIUsageRequest(
            idempotency_key=key,
            user_id=account.owner_id,
            account_pk=account.id,
            account_id=account.account_id,
            period_start=period,
            source_message_id=source_id,
            chat_id=chat_id,
            status="reserved",
            requested_at=now,
        )
        session.add(request)
        user_usage.reserved_replies += 1
        account_usage.reserved_replies += 1
        await session.flush()
        return AIReservation(request.id, key)

    @staticmethod
    async def attach_generation(
        session: AsyncSession,
        request_id: int,
        *,
        model_name: str | None,
        provider_name: str | None,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        input_text: str,
        output_text: str,
        token_source: str | None,
    ) -> None:
        async with session.begin():
            request = await session.scalar(select(AIUsageRequest).where(AIUsageRequest.id == request_id).with_for_update())
            if not request or request.status != "reserved":
                return
            request.model_name = model_name
            request.provider_name = provider_name
            request.latency_ms = latency_ms
            request.input_tokens = input_tokens if input_tokens is not None else estimate_tokens(input_text)
            request.output_tokens = output_tokens if output_tokens is not None else estimate_tokens(output_text)
            if token_source in ("estimated", "mixed_estimated"):
                request.token_source = token_source
            elif input_tokens is None and output_tokens is None:
                request.token_source = "estimated"
            elif input_tokens is not None and output_tokens is not None:
                request.token_source = "upstream"
            else:
                request.token_source = "mixed_estimated"
            request.estimated_cost = estimate_cost(request.input_tokens, request.output_tokens)

    @staticmethod
    async def commit(session: AsyncSession, request_id: int, auto_reply_log_id: int | None = None) -> bool:
        now = get_beijing_now_naive()
        async with session.begin():
            request = await session.scalar(select(AIUsageRequest).where(AIUsageRequest.id == request_id).with_for_update())
            if not request or request.status == "committed":
                return bool(request and request.status == "committed")
            if request.status != "reserved":
                return False
            user_usage = await session.scalar(
                select(AIUserMonthlyUsage).where(
                    AIUserMonthlyUsage.user_id == request.user_id,
                    AIUserMonthlyUsage.period_start == request.period_start,
                ).with_for_update()
            )
            account_usage = await session.scalar(
                select(AIAccountMonthlyUsage).where(
                    AIAccountMonthlyUsage.account_pk == request.account_pk,
                    AIAccountMonthlyUsage.period_start == request.period_start,
                ).with_for_update()
            )
            cost = Decimal(request.estimated_cost or 0)
            user_usage.reserved_replies = max(0, user_usage.reserved_replies - 1)
            user_usage.effective_replies += 1
            user_usage.estimated_cost = Decimal(user_usage.estimated_cost or 0) + cost
            account_usage.reserved_replies = max(0, account_usage.reserved_replies - 1)
            account_usage.effective_replies += 1
            account_usage.estimated_cost = Decimal(account_usage.estimated_cost or 0) + cost
            request.status = "committed"
            request.auto_reply_log_id = auto_reply_log_id
            request.committed_at = now
            await AIUsageService._mark_thresholds(session, request.user_id, user_usage, now)
            return True

    @staticmethod
    async def release(session: AsyncSession, request_id: int, reason: str) -> bool:
        async with session.begin():
            request = await session.scalar(select(AIUsageRequest).where(AIUsageRequest.id == request_id).with_for_update())
            if not request or request.status != "reserved":
                return False
            user_usage = await session.scalar(
                select(AIUserMonthlyUsage).where(
                    AIUserMonthlyUsage.user_id == request.user_id,
                    AIUserMonthlyUsage.period_start == request.period_start,
                ).with_for_update()
            )
            account_usage = await session.scalar(
                select(AIAccountMonthlyUsage).where(
                    AIAccountMonthlyUsage.account_pk == request.account_pk,
                    AIAccountMonthlyUsage.period_start == request.period_start,
                ).with_for_update()
            )
            user_usage.reserved_replies = max(0, user_usage.reserved_replies - 1)
            account_usage.reserved_replies = max(0, account_usage.reserved_replies - 1)
            request.status = "released"
            request.release_reason = reason[:64]
            request.released_at = get_beijing_now_naive()
            return True

    @staticmethod
    async def _ensure_usage_rows(session: AsyncSession, account: XYAccount, period: date) -> None:
        await session.execute(
            text("""INSERT INTO xy_ai_user_monthly_usage
                (user_id, period_start, effective_replies, reserved_replies, estimated_cost, created_at, updated_at)
                VALUES (:user_id, :period_start, 0, 0, 0, NOW(), NOW())
                ON DUPLICATE KEY UPDATE updated_at = updated_at"""),
            {"user_id": account.owner_id, "period_start": period},
        )
        await session.execute(
            text("""INSERT INTO xy_ai_account_monthly_usage
                (account_pk, user_id, period_start, effective_replies, reserved_replies, estimated_cost, created_at, updated_at)
                VALUES (:account_pk, :user_id, :period_start, 0, 0, 0, NOW(), NOW())
                ON DUPLICATE KEY UPDATE updated_at = updated_at"""),
            {"account_pk": account.id, "user_id": account.owner_id, "period_start": period},
        )

    @staticmethod
    def _check_quota(usage: Any, quota: int | None, scope: str) -> None:
        if quota is not None and usage.effective_replies + usage.reserved_replies >= quota:
            raise AIQuotaExceededError(f"AI {scope} monthly quota exhausted")

    @staticmethod
    def _get_user_quota(config: Any) -> int | None:
        if not config:
            return None
        return int(config.package_quota or 0) + int(config.independent_quota or 0)

    @staticmethod
    async def _mark_thresholds(session, user_id, usage, now):
        config = await session.scalar(select(AIQuotaConfig).where(AIQuotaConfig.user_id == user_id))
        quota = AIUsageService._get_user_quota(config)
        if not quota or quota <= 0:
            return
        ratio = usage.effective_replies / quota
        if ratio >= 1 and usage.warned_100_at is None:
            usage.warned_100_at = now
            logger.warning(f"AI quota reached 100% for user_id={user_id}, period={usage.period_start}")
        elif ratio >= 0.8 and usage.warned_80_at is None:
            usage.warned_80_at = now
            logger.warning(f"AI quota reached 80% for user_id={user_id}, period={usage.period_start}")
