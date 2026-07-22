from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.ai_usage import AIQuotaConfig
from common.models.billing import BillingPlan, EntitlementLedger, UserSubscription
from common.models.user import User
from common.services.entitlement_grant_service import EntitlementGrantService
from common.utils.time_utils import get_beijing_now_naive


class SubscriptionLifecycleError(RuntimeError):
    pass


class SubscriptionLifecycleService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def expire_user_if_due(
        self, user_id: int, now: datetime | None = None
    ) -> bool:
        now = now or get_beijing_now_naive()
        subscription = await self.session.scalar(
            select(UserSubscription)
            .where(
                UserSubscription.user_id == user_id,
                UserSubscription.status == "active",
                UserSubscription.plan_code != "free",
                UserSubscription.expires_at.is_not(None),
                UserSubscription.expires_at <= now,
            )
            .with_for_update()
        )
        if not subscription:
            return False
        if subscription.pending_plan_id is not None:
            await self._activate_pending_plan(subscription, now)
        else:
            free_plan = await self._get_free_plan()
            await self._downgrade_to_free(subscription, free_plan, now)
        return True

    async def expire_due_subscriptions(
        self,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        now = now or get_beijing_now_naive()
        subscriptions = list(
            await self.session.scalars(
                select(UserSubscription)
                .where(
                    UserSubscription.status == "active",
                    UserSubscription.plan_code != "free",
                    UserSubscription.expires_at.is_not(None),
                    UserSubscription.expires_at <= now,
                )
                .order_by(UserSubscription.expires_at, UserSubscription.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        if not subscriptions:
            return 0
        free_plan: BillingPlan | None = None
        for subscription in subscriptions:
            if subscription.pending_plan_id is not None:
                await self._activate_pending_plan(subscription, now)
            else:
                if free_plan is None:
                    free_plan = await self._get_free_plan()
                await self._downgrade_to_free(subscription, free_plan, now)
        return len(subscriptions)

    async def _get_free_plan(self) -> BillingPlan:
        free_plan = await self.session.scalar(
            select(BillingPlan).where(
                BillingPlan.code == "free",
                BillingPlan.enabled.is_(True),
            )
        )
        if not free_plan:
            raise SubscriptionLifecycleError("Free billing plan is not initialized")
        return free_plan

    async def _activate_pending_plan(
        self, subscription: UserSubscription, now: datetime
    ) -> None:
        """到期时激活待生效降级套餐，同事务锁定用户、订阅与额度配置。"""
        old_plan_code = subscription.plan_code
        old_expires_at = subscription.expires_at
        user = await self.session.scalar(
            select(User).where(User.id == subscription.user_id).with_for_update()
        )
        if not user:
            raise SubscriptionLifecycleError("Subscription user not found")
        quota_config = await self.session.scalar(
            select(AIQuotaConfig)
            .where(AIQuotaConfig.user_id == subscription.user_id)
            .with_for_update()
        )
        if not quota_config:
            quota_config = AIQuotaConfig(
                user_id=subscription.user_id,
                package_quota=0,
                independent_quota=0,
            )
            self.session.add(quota_config)

        plan_id = int(subscription.pending_plan_id)
        plan_code = str(subscription.pending_plan_code)
        billing_cycle = subscription.pending_billing_cycle
        duration_months = int(subscription.pending_duration_months or 1)
        account_limit = int(subscription.pending_account_limit or 0)
        monthly_ai_quota = int(subscription.pending_monthly_ai_quota or 0)
        ai_unlimited = bool(subscription.pending_ai_unlimited)
        feature_snapshot = EntitlementGrantService._normalize_feature_flags(
            subscription.pending_feature_snapshot or []
        )

        period_start, period_end = EntitlementGrantService._period_bounds(now)
        new_expires_at = EntitlementGrantService._add_months(now, duration_months)

        subscription.plan_id = plan_id
        subscription.plan_code = plan_code
        subscription.billing_cycle = billing_cycle
        subscription.status = "active"
        subscription.account_limit = account_limit
        subscription.monthly_ai_quota = monthly_ai_quota
        subscription.ai_unlimited = ai_unlimited
        subscription.feature_snapshot = feature_snapshot
        subscription.current_period_start = period_start
        subscription.current_period_end = period_end
        subscription.starts_at = now
        subscription.expires_at = new_expires_at
        # 清空全部 pending_plan_* 字段
        subscription.pending_plan_id = None
        subscription.pending_plan_code = None
        subscription.pending_billing_cycle = None
        subscription.pending_duration_months = None
        subscription.pending_account_limit = None
        subscription.pending_monthly_ai_quota = None
        subscription.pending_ai_unlimited = False
        subscription.pending_feature_snapshot = None
        subscription.pending_source = None
        subscription.source_order_id = None

        user.account_limit = account_limit
        quota_config.package_quota = 0 if ai_unlimited else monthly_ai_quota

        self.session.add(
            EntitlementLedger(
                user_id=subscription.user_id,
                event_type="pending_plan_activated",
                entitlement_type="plan_activate",
                quantity=0 if ai_unlimited else monthly_ai_quota,
                order_id=None,
                grant_id=None,
                idempotency_key=(
                    f"subscription:activated:{subscription.id}:"
                    f"{old_expires_at.isoformat() if old_expires_at else 'unknown'}"
                ),
                details={
                    "from_plan": old_plan_code,
                    "to_plan": plan_code,
                    "ai_unlimited": ai_unlimited,
                    "expired_at": old_expires_at.isoformat()
                    if old_expires_at
                    else None,
                },
            )
        )

    async def _downgrade_to_free(
        self,
        subscription: UserSubscription,
        free_plan: BillingPlan,
        now: datetime,
    ) -> None:
        """降为免费版。不触碰独立加量包/无限包 grant——若有效无限包仍存在，
        ``AIUsageService._has_active_unlimited`` 仍会判定为无限，不会因套餐降级
        错误关闭仍有效的独立无限包。"""
        old_plan_code = subscription.plan_code
        old_expires_at = subscription.expires_at
        user = await self.session.scalar(
            select(User).where(User.id == subscription.user_id).with_for_update()
        )
        if not user:
            raise SubscriptionLifecycleError("Subscription user not found")
        quota_config = await self.session.scalar(
            select(AIQuotaConfig)
            .where(AIQuotaConfig.user_id == subscription.user_id)
            .with_for_update()
        )
        if not quota_config:
            quota_config = AIQuotaConfig(
                user_id=subscription.user_id,
                package_quota=0,
                independent_quota=0,
            )
            self.session.add(quota_config)

        period_start = now.date().replace(day=1)
        next_month = (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        user.account_limit = int(free_plan.account_limit)
        quota_config.package_quota = int(free_plan.monthly_ai_quota)
        subscription.plan_id = free_plan.id
        subscription.plan_code = free_plan.code
        subscription.billing_cycle = None
        subscription.status = "active"
        subscription.account_limit = int(free_plan.account_limit)
        subscription.monthly_ai_quota = int(free_plan.monthly_ai_quota)
        subscription.ai_unlimited = False
        subscription.feature_snapshot = (
            EntitlementGrantService._normalize_feature_flags(free_plan.feature_flags)
        )
        subscription.current_period_start = period_start
        subscription.current_period_end = next_month - timedelta(days=1)
        subscription.starts_at = now
        subscription.expires_at = None
        subscription.pending_plan_id = None
        subscription.source_order_id = None
        self.session.add(
            EntitlementLedger(
                user_id=subscription.user_id,
                event_type="subscription_expired",
                entitlement_type="plan_downgrade",
                quantity=0,
                order_id=None,
                grant_id=None,
                idempotency_key=(
                    f"subscription:expired:{subscription.id}:"
                    f"{old_expires_at.isoformat() if old_expires_at else 'unknown'}"
                ),
                details={
                    "from_plan": old_plan_code,
                    "to_plan": free_plan.code,
                    "expired_at": old_expires_at.isoformat()
                    if old_expires_at
                    else None,
                },
            )
        )
