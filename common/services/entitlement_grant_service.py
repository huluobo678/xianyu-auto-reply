"""通用权益发放服务

从 ``BillingEntitlementService`` 抽取的通用权益发放核心，供支付宝订单与兑换码
两条入口复用。支付宝订单经 ``BillingEntitlementService.grant_locked_order`` 薄包装
调用本服务；兑换码经 ``RedemptionService.redeem`` 调用本服务。

规则要点（详见阶段二设计）：
- 套餐：同套餐续期顺延、升级立即生效不补差价、降级写入 pending 待到期生效；
  季卡兑换只发当月额度，后续由调度按月发放；企业版转为无限权益且不创建次数 grant。
- 加量包：普通包创建次数 grant；无限包创建 ai_unlimited=1 的 grant（30 天）。
- 幂等：账本按 ``ledger:{user_id}:{idempotency_key}`` 去重、加量包 grant 按
  ``grant:{user_id}:{idempotency_key}`` 去重（均按用户隔离，避免跨用户命中）；
  套餐当月额度按 ``sub-monthly:{user_id}:{period}`` 去重，避免同一自然月重复发放。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.ai_usage import AIQuotaConfig
from common.models.billing import AIQuotaGrant, EntitlementLedger, UserSubscription
from common.models.user import User


class EntitlementGrantError(RuntimeError):
    """权益发放业务错误（如已有待生效降级时再兑换跨套餐码）。"""


# 套餐等级排序，用于判定升级/降级。仅支持目录内的套餐编码；
# 未知编码回退到 snapshot 的 sort_order，再回退到 0（视为最低）。
_PLAN_RANK = {
    "free": 0,
    "standard": 10,
    "merchant": 20,
    "enterprise": 30,
}


@dataclass(frozen=True)
class GrantResult:
    """一次权益发放的结果，供调用方写审计记录。"""

    action: str  # new / renew / upgrade / pending_downgrade / package
    grant_id: int | None
    ledger_id: int | None
    subscription_id: int | None
    expires_at: datetime | None
    ai_unlimited: bool


class EntitlementGrantService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------- 公共入口 ----------

    async def grant_plan(
        self,
        user_id: int,
        plan_snapshot: dict,
        billing_cycle: str,
        duration_months: int,
        idempotency_key: str,
        source: dict,
        now: datetime,
    ) -> GrantResult:
        """发放套餐权益（新建/续期/升级/待生效降级）。"""
        plan_code = str(plan_snapshot["plan_code"])
        account_limit = int(plan_snapshot["account_limit"])
        monthly_ai_quota = int(plan_snapshot.get("monthly_ai_quota", 0))
        ai_unlimited = bool(plan_snapshot.get("ai_unlimited", False))
        feature_flags = self._normalize_feature_flags(
            plan_snapshot.get("feature_flags", [])
        )
        plan_id = int(plan_snapshot["plan_id"])

        subscription = await self.session.scalar(
            select(UserSubscription)
            .where(UserSubscription.user_id == user_id)
            .with_for_update()
        )
        user = await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if not user:
            raise EntitlementGrantError("Entitlement user not found")

        action, cur_rank = self._resolve_plan_action(subscription, plan_code, now)
        new_rank = self._plan_rank(plan_code, plan_snapshot)

        if action == "pending_downgrade":
            return await self._apply_pending_downgrade(
                subscription=subscription,
                plan_id=plan_id,
                plan_code=plan_code,
                billing_cycle=billing_cycle,
                duration_months=duration_months,
                account_limit=account_limit,
                monthly_ai_quota=monthly_ai_quota,
                ai_unlimited=ai_unlimited,
                feature_flags=feature_flags,
                idempotency_key=idempotency_key,
                source=source,
                now=now,
            )

        # 续期/升级/新建：统一计算新到期日
        active_expiry = (
            subscription.expires_at
            if subscription
            and subscription.expires_at
            and subscription.expires_at > now
            else None
        )
        base = active_expiry or now
        expires_at = self._add_months(base, duration_months)
        period_start, period_end = self._period_bounds(now)

        if not subscription:
            subscription = UserSubscription(
                user_id=user_id,
                plan_id=plan_id,
                plan_code=plan_code,
                billing_cycle=billing_cycle,
                status="active",
                account_limit=account_limit,
                monthly_ai_quota=monthly_ai_quota,
                ai_unlimited=ai_unlimited,
                feature_snapshot=feature_flags,
                current_period_start=period_start,
                current_period_end=period_end,
                starts_at=now,
                expires_at=expires_at,
                source_order_id=source.get("order_id"),
            )
            self.session.add(subscription)
            await self.session.flush()
        else:
            subscription.plan_id = plan_id
            subscription.plan_code = plan_code
            subscription.billing_cycle = billing_cycle
            subscription.status = "active"
            subscription.account_limit = account_limit
            subscription.monthly_ai_quota = monthly_ai_quota
            subscription.ai_unlimited = ai_unlimited
            subscription.feature_snapshot = feature_flags
            subscription.current_period_start = period_start
            subscription.current_period_end = period_end
            if not active_expiry:
                subscription.starts_at = now
            subscription.expires_at = expires_at
            # 续期/升级清除任何已存在的待生效降级
            subscription.pending_plan_id = None
            subscription.pending_plan_code = None
            subscription.pending_billing_cycle = None
            subscription.pending_duration_months = None
            subscription.pending_account_limit = None
            subscription.pending_monthly_ai_quota = None
            subscription.pending_ai_unlimited = False
            subscription.pending_feature_snapshot = None
            subscription.pending_source = None
            subscription.source_order_id = source.get("order_id")

        user.account_limit = account_limit
        quota_config = await self._get_or_create_quota_config(user_id)
        quota_config.package_quota = 0 if ai_unlimited else monthly_ai_quota

        grant_id = None
        if not ai_unlimited:
            grant_id = await self._ensure_monthly_subscription_grant(
                user_id=user_id,
                subscription_id=subscription.id,
                monthly_ai_quota=monthly_ai_quota,
                period_start=period_start,
                period_end=period_end,
                now=now,
                source=source,
            )

        ledger_id = await self._ensure_ledger(
            user_id=user_id,
            event_type=source.get("event_type", "plan_grant"),
            entitlement_type="monthly_ai_quota"
            if not ai_unlimited
            else "unlimited_plan",
            quantity=0 if ai_unlimited else monthly_ai_quota,
            grant_id=grant_id,
            idempotency_key=idempotency_key,
            details={
                "plan_code": plan_code,
                "billing_cycle": billing_cycle,
                "duration_months": duration_months,
                "account_limit": account_limit,
                "ai_unlimited": ai_unlimited,
                "action": action,
                "new_rank": new_rank,
                "cur_rank": cur_rank,
            },
            source=source,
        )

        return GrantResult(
            action=action,
            grant_id=grant_id,
            ledger_id=ledger_id,
            subscription_id=subscription.id,
            expires_at=expires_at,
            ai_unlimited=ai_unlimited,
        )

    async def grant_quota_package(
        self,
        user_id: int,
        package_snapshot: dict,
        idempotency_key: str,
        source: dict,
        now: datetime,
    ) -> GrantResult:
        """发放 AI 加量包权益。普通包创建次数 grant；无限包创建 ai_unlimited grant。"""
        ai_unlimited = bool(package_snapshot.get("ai_unlimited", False))
        total_quota = int(package_snapshot.get("total_quota", 0))
        validity_days = int(package_snapshot.get("validity_days", 0))
        expires_at = now + timedelta(days=validity_days) if validity_days else None

        user = await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if not user:
            raise EntitlementGrantError("Entitlement user not found")
        # 加量包不影响套餐 package_quota，仅确保配置行存在
        await self._get_or_create_quota_config(user_id)

        grant_key = f"grant:{user_id}:{idempotency_key}"
        grant = await self.session.scalar(
            select(AIQuotaGrant).where(AIQuotaGrant.idempotency_key == grant_key)
        )
        if not grant:
            grant = AIQuotaGrant(
                user_id=user_id,
                grant_type="quota_package",
                source_id=source.get("order_id") or source.get("code_id"),
                idempotency_key=grant_key,
                total_quota=0 if ai_unlimited else total_quota,
                remaining_quota=0 if ai_unlimited else total_quota,
                ai_unlimited=ai_unlimited,
                starts_at=now,
                expires_at=expires_at,
                status="active",
            )
            self.session.add(grant)
            await self.session.flush()

        ledger_id = await self._ensure_ledger(
            user_id=user_id,
            event_type=source.get("event_type", "package_grant"),
            entitlement_type="unlimited_ai_quota"
            if ai_unlimited
            else "independent_ai_quota",
            quantity=0 if ai_unlimited else total_quota,
            grant_id=grant.id,
            idempotency_key=idempotency_key,
            details={
                "package_code": package_snapshot.get("package_code"),
                "base_quota": int(package_snapshot.get("base_quota", 0)),
                "bonus_quota": int(package_snapshot.get("bonus_quota", 0)),
                "validity_days": validity_days,
                "ai_unlimited": ai_unlimited,
            },
            source=source,
        )

        return GrantResult(
            action="package",
            grant_id=grant.id,
            ledger_id=ledger_id,
            subscription_id=None,
            expires_at=expires_at,
            ai_unlimited=ai_unlimited,
        )

    async def grant_scheduled_monthly_quota(
        self, subscription: UserSubscription, now: datetime
    ) -> int | None:
        """调度入口：为活动季卡订阅发放当月套餐额度（幂等）。

        - 普通季卡：设置当月 ``package_quota`` 并按 ``用户+订阅+自然月`` 幂等键创建
          当月套餐额度 grant；月底失效，重复执行不重复发放。
        - 企业版季卡（``ai_unlimited=True``）：跳过次数 grant，订阅无限状态保持不变。

        Returns:
            当月 grant id（企业版返回 None）。
        """
        if getattr(subscription, "ai_unlimited", False):
            return None
        monthly_ai_quota = int(subscription.monthly_ai_quota or 0)
        period_start, period_end = self._period_bounds(now)
        quota_config = await self._get_or_create_quota_config(subscription.user_id)
        quota_config.package_quota = monthly_ai_quota
        return await self._ensure_monthly_subscription_grant(
            user_id=subscription.user_id,
            subscription_id=int(subscription.id),
            monthly_ai_quota=monthly_ai_quota,
            period_start=period_start,
            period_end=period_end,
            now=now,
            source={"kind": "scheduler"},
        )

    # ---------- 内部：动作判定与降级待生效 ----------

    @staticmethod
    def _plan_rank(plan_code: str, snapshot: dict) -> int:
        if plan_code in _PLAN_RANK:
            return _PLAN_RANK[plan_code]
        order = snapshot.get("sort_order")
        return int(order) if isinstance(order, (int, float)) else 0

    @classmethod
    def _resolve_plan_action(
        cls,
        subscription: UserSubscription | None,
        new_plan_code: str,
        now: datetime,
    ) -> tuple[str, int]:
        """返回 (action, cur_rank)。无有效付费订阅时视为新建（从免费/过期起算）。"""
        if (
            not subscription
            or subscription.status != "active"
            or subscription.plan_code == "free"
            or not subscription.expires_at
            or subscription.expires_at <= now
        ):
            return "new", 0
        cur_rank = cls._plan_rank(subscription.plan_code, {})
        if new_plan_code == subscription.plan_code:
            return "renew", cur_rank
        new_rank = cls._plan_rank(new_plan_code, {})
        if new_rank > cur_rank:
            return "upgrade", cur_rank
        return "pending_downgrade", cur_rank

    async def _apply_pending_downgrade(
        self,
        *,
        subscription: UserSubscription,
        plan_id: int,
        plan_code: str,
        billing_cycle: str,
        duration_months: int,
        account_limit: int,
        monthly_ai_quota: int,
        ai_unlimited: bool,
        feature_flags: list[str],
        idempotency_key: str,
        source: dict,
        now: datetime,
    ) -> GrantResult:
        # 已有待生效降级时拒绝第二个跨套餐码，提示联系管理员
        if subscription.pending_plan_id is not None:
            raise EntitlementGrantError(
                "A pending plan change already exists; contact the administrator"
            )
        subscription.pending_plan_id = plan_id
        subscription.pending_plan_code = plan_code
        subscription.pending_billing_cycle = billing_cycle
        subscription.pending_duration_months = duration_months
        subscription.pending_account_limit = account_limit
        subscription.pending_monthly_ai_quota = monthly_ai_quota
        subscription.pending_ai_unlimited = ai_unlimited
        subscription.pending_feature_snapshot = feature_flags
        subscription.pending_source = source.get("kind")

        ledger_id = await self._ensure_ledger(
            user_id=subscription.user_id,
            event_type=source.get("event_type", "pending_downgrade"),
            entitlement_type="plan_pending_downgrade",
            quantity=0,
            grant_id=None,
            idempotency_key=idempotency_key,
            details={
                "from_plan": subscription.plan_code,
                "pending_plan": plan_code,
                "ai_unlimited": ai_unlimited,
            },
            source=source,
        )
        return GrantResult(
            action="pending_downgrade",
            grant_id=None,
            ledger_id=ledger_id,
            subscription_id=subscription.id,
            expires_at=subscription.expires_at,
            ai_unlimited=subscription.ai_unlimited or False,
        )

    # ---------- 内部：grant / ledger / quota_config ----------

    async def _ensure_monthly_subscription_grant(
        self,
        *,
        user_id: int,
        subscription_id: int,
        monthly_ai_quota: int,
        period_start,
        period_end,
        now: datetime,
        source: dict,
    ) -> int | None:
        """按 ``sub-monthly:{user}:{period}`` 去重创建当月套餐额度审计 grant。

        套餐 grant 的 grant_type 为 ``subscription``，不会被 ``_reserve_user_quota``
        消费（其仅消费 signup_bonus / quota_package）；实际月度额度由
        ``AIQuotaConfig.package_quota`` 承载、由月度 effective_replies 计数器统计。
        同一自然月续期只延长有效期，不重复创建当月 grant。
        """
        monthly_key = (
            f"sub-monthly:{user_id}:{subscription_id}:{period_start.isoformat()}"
        )
        existing = await self.session.scalar(
            select(AIQuotaGrant).where(AIQuotaGrant.idempotency_key == monthly_key)
        )
        if existing:
            return existing.id
        grant = AIQuotaGrant(
            user_id=user_id,
            grant_type="subscription",
            source_id=subscription_id,
            idempotency_key=monthly_key,
            total_quota=monthly_ai_quota,
            remaining_quota=monthly_ai_quota,
            ai_unlimited=False,
            starts_at=now,
            expires_at=datetime.combine(period_end, datetime.max.time()),
            status="active",
        )
        self.session.add(grant)
        await self.session.flush()
        return grant.id

    async def _ensure_ledger(
        self,
        *,
        user_id: int,
        event_type: str,
        entitlement_type: str,
        quantity: int,
        grant_id: int | None,
        idempotency_key: str,
        details: dict,
        source: dict,
    ) -> int | None:
        # 幂等键按用户隔离：同一兑换幂等键在不同用户下不会互相命中账本。
        ledger_key = f"ledger:{user_id}:{idempotency_key}"
        existing = await self.session.scalar(
            select(EntitlementLedger).where(
                EntitlementLedger.idempotency_key == ledger_key
            )
        )
        if existing:
            return existing.id
        # 权益流水保留可追溯来源：兑换码 ID / 批次 ID 写入 details，
        # 便于从权益流水反查到具体兑换码与批次（不写入完整明文兑换码）。
        traceable = dict(details)
        if source.get("code_id") is not None:
            traceable["code_id"] = source["code_id"]
        if source.get("batch_id") is not None:
            traceable["batch_id"] = source["batch_id"]
        ledger = EntitlementLedger(
            user_id=user_id,
            event_type=event_type,
            entitlement_type=entitlement_type,
            quantity=quantity,
            order_id=source.get("order_id"),
            grant_id=grant_id,
            idempotency_key=ledger_key,
            details=traceable,
        )
        self.session.add(ledger)
        await self.session.flush()
        return ledger.id

    async def _get_or_create_quota_config(self, user_id: int) -> AIQuotaConfig:
        config = await self.session.scalar(
            select(AIQuotaConfig)
            .where(AIQuotaConfig.user_id == user_id)
            .with_for_update()
        )
        if not config:
            config = AIQuotaConfig(
                user_id=user_id, package_quota=0, independent_quota=0
            )
            self.session.add(config)
            await self.session.flush()
        return config

    # ---------- 静态工具（供 BillingEntitlementService / Lifecycle 复用） ----------

    @staticmethod
    def _normalize_feature_flags(value: object) -> list[str]:
        if isinstance(value, dict):
            return [str(key) for key, enabled in value.items() if enabled]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        if value is None:
            return []
        raise EntitlementGrantError("Plan feature flags must be a list or object")

    @staticmethod
    def _add_months(value: datetime, months: int) -> datetime:
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)

    @staticmethod
    def _period_bounds(now: datetime) -> tuple[Any, Any]:
        """返回当前自然月的 (period_start_date, period_end_date)。"""
        period_start = now.date().replace(day=1)
        next_month_first = EntitlementGrantService._add_months(
            datetime(now.year, now.month, 1), 1
        )
        period_end = (next_month_first - timedelta(days=1)).date()
        return period_start, period_end
