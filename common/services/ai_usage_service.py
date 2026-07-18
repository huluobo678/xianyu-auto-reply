from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from common.models.ai_usage import AIAccountMonthlyUsage, AIAccountQuotaConfig
from common.models.ai_usage import AIQuotaConfig, AIUsageRequest, AIUserMonthlyUsage
from common.models.billing import AIQuotaGrant
from common.models.xy_account import XYAccount
from common.utils.time_utils import get_beijing_now_naive

DEFAULT_ACCOUNT_RPM = 60
DEFAULT_ACCOUNT_CONCURRENCY = 1
RESERVATION_TIMEOUT = timedelta(minutes=15)
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
            request = await session.scalar(
                select(AIUsageRequest).where(AIUsageRequest.idempotency_key == key)
            )
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
        stale_ids = await AIUsageService._lock_stale_reservation_ids(
            session, account.id, account.owner_id, period, now
        )
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
        await AIUsageService._release_stale_reservations_locked(
            session, account, user_usage, account_usage, stale_ids, now
        )
        quota_grant_id = await AIUsageService._reserve_user_quota(
            session, account.owner_id, user_usage, user_config, now
        )
        AIUsageService._check_quota(account_usage, account_config.monthly_quota if account_config else None, "account")
        concurrency = account_config.max_concurrency if account_config else DEFAULT_ACCOUNT_CONCURRENCY
        if account_usage.reserved_replies >= max(1, concurrency):
            raise AIAccountConcurrencyError("AI account concurrency limit reached")
        rpm = account_config.requests_per_minute if account_config else DEFAULT_ACCOUNT_RPM
        recent_count = await session.scalar(
            select(func.count(AIUsageRequest.id)).where(
                AIUsageRequest.account_pk == account.id,
                AIUsageRequest.created_at >= now - timedelta(minutes=1),
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
            quota_grant_id=quota_grant_id,
            requested_at=now,
        )
        session.add(request)
        user_usage.reserved_replies += 1
        account_usage.reserved_replies += 1
        await session.flush()
        return AIReservation(request.id, key)

    @staticmethod
    async def _lock_stale_reservation_ids(
        session: AsyncSession,
        account_pk: int,
        user_id: int,
        period: date,
        now: datetime,
    ) -> list[int]:
        return list(
            await session.scalars(
                select(AIUsageRequest.id)
                .where(
                    AIUsageRequest.account_pk == account_pk,
                    AIUsageRequest.user_id == user_id,
                    AIUsageRequest.period_start == period,
                    AIUsageRequest.status == "reserved",
                    AIUsageRequest.requested_at < now - RESERVATION_TIMEOUT,
                )
                .with_for_update()
            )
        )

    @staticmethod
    async def _release_stale_reservations_locked(
        session: AsyncSession,
        account: XYAccount,
        user_usage: AIUserMonthlyUsage,
        account_usage: AIAccountMonthlyUsage,
        stale_ids: list[int],
        now: datetime,
    ) -> int:
        if not stale_ids:
            return 0

        stale_count = len(stale_ids)
        await AIUsageService._restore_quota_grants(session, stale_ids)
        await session.execute(
            update(AIUsageRequest)
            .where(
                AIUsageRequest.id.in_(stale_ids),
                AIUsageRequest.status == "reserved",
            )
            .values(
                status="released",
                release_reason="reservation_expired",
                released_at=now,
            )
        )
        user_usage.reserved_replies = max(0, user_usage.reserved_replies - stale_count)
        account_usage.reserved_replies = max(0, account_usage.reserved_replies - stale_count)
        logger.warning(
            f"Released {stale_count} stale AI reservations for account_pk={account.id}"
        )
        return stale_count

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
            await AIUsageService._restore_quota_grant(session, getattr(request, "quota_grant_id", None))
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
    async def _reserve_user_quota(
        session: AsyncSession,
        user_id: int,
        usage: AIUserMonthlyUsage,
        config: AIQuotaConfig | None,
        now: datetime,
    ) -> int | None:
        base_quota = AIUsageService._get_user_quota(config)
        if base_quota is None:
            return None
        if usage.effective_replies + usage.reserved_replies < base_quota:
            return None

        grant = await session.scalar(
            select(AIQuotaGrant)
            .where(
                AIQuotaGrant.user_id == user_id,
                AIQuotaGrant.grant_type.in_(("signup_bonus", "quota_package")),
                AIQuotaGrant.status == "active",
                AIQuotaGrant.starts_at <= now,
                or_(AIQuotaGrant.expires_at.is_(None), AIQuotaGrant.expires_at > now),
                AIQuotaGrant.remaining_quota > 0,
            )
            .order_by(
                case((AIQuotaGrant.grant_type == "signup_bonus", 0), else_=1),
                AIQuotaGrant.expires_at.is_(None),
                AIQuotaGrant.expires_at,
                AIQuotaGrant.id,
            )
            .with_for_update()
        )
        if not grant:
            raise AIQuotaExceededError("AI user quota exhausted")
        grant.remaining_quota -= 1
        return grant.id

    @staticmethod
    async def _restore_quota_grants(
        session: AsyncSession,
        request_ids: list[int],
    ) -> None:
        grant_ids = list(await session.scalars(
            select(AIUsageRequest.quota_grant_id).where(
                AIUsageRequest.id.in_(request_ids),
                AIUsageRequest.status == "reserved",
                AIUsageRequest.quota_grant_id.is_not(None),
            )
        ))
        for grant_id in grant_ids:
            await AIUsageService._restore_quota_grant(session, grant_id)

    @staticmethod
    async def _restore_quota_grant(
        session: AsyncSession,
        grant_id: int | None,
    ) -> None:
        if grant_id is None:
            return
        grant = await session.scalar(
            select(AIQuotaGrant)
            .where(AIQuotaGrant.id == grant_id)
            .with_for_update()
        )
        if grant:
            grant.remaining_quota = min(
                int(grant.total_quota),
                int(grant.remaining_quota) + 1,
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
