from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.billing import BillingPlan, UserSubscription
from common.services.billing_catalog import DEFAULT_FEATURE_FLAGS
from common.utils.time_utils import get_beijing_now_naive

FEATURE_IMPLICATIONS: dict[str, set[str]] = {
    'batch_publish': {'single_publish'},
}


class SubscriptionFeatureService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def normalize_feature_flags(value: object) -> set[str]:
        if isinstance(value, dict):
            return {str(key) for key, enabled in value.items() if enabled}
        if isinstance(value, (list, tuple, set)):
            return {str(item) for item in value}
        return set()

    @classmethod
    def expand_feature_flags(cls, value: object) -> set[str]:
        features = cls.normalize_feature_flags(value)
        pending = list(features)
        while pending:
            feature = pending.pop()
            for implied in FEATURE_IMPLICATIONS.get(feature, set()):
                if implied not in features:
                    features.add(implied)
                    pending.append(implied)
        return features

    async def get_entitlements(self, user_id: int) -> dict[str, Any]:
        now = get_beijing_now_naive()
        subscription = await self.session.scalar(
            select(UserSubscription).where(
                UserSubscription.user_id == user_id,
                UserSubscription.status == 'active',
                UserSubscription.starts_at <= now,
                or_(UserSubscription.expires_at.is_(None), UserSubscription.expires_at > now),
            )
        )
        if subscription:
            return {
                'plan_code': subscription.plan_code,
                'account_limit': int(subscription.account_limit),
                'monthly_ai_quota': int(subscription.monthly_ai_quota),
                'features': sorted(self.expand_feature_flags(subscription.feature_snapshot)),
                'expires_at': subscription.expires_at,
                'source': 'subscription',
            }

        free_plan = await self.session.scalar(
            select(BillingPlan).where(
                BillingPlan.code == 'free',
                BillingPlan.enabled.is_(True),
            )
        )
        feature_flags = free_plan.feature_flags if free_plan else DEFAULT_FEATURE_FLAGS['free']
        return {
            'plan_code': 'free',
            'account_limit': int(free_plan.account_limit) if free_plan else 1,
            'monthly_ai_quota': int(free_plan.monthly_ai_quota) if free_plan else 0,
            'features': sorted(self.expand_feature_flags(feature_flags)),
            'expires_at': None,
            'source': 'free_fallback',
        }

    async def has_any_feature(self, user_id: int, feature_codes: tuple[str, ...]) -> bool:
        entitlements = await self.get_entitlements(user_id)
        features = set(entitlements['features'])
        return any(feature in features for feature in feature_codes)
