from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.ai_usage import AIQuotaConfig
from common.models.billing import BillingPlan, EntitlementLedger, UserSubscription
from common.models.user import User
from common.services.billing_entitlement_service import BillingEntitlementService
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
        free_plan = await self._get_free_plan()
        for subscription in subscriptions:
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

    async def _downgrade_to_free(
        self,
        subscription: UserSubscription,
        free_plan: BillingPlan,
        now: datetime,
    ) -> None:
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
        subscription.feature_snapshot = (
            BillingEntitlementService._normalize_feature_flags(free_plan.feature_flags)
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
