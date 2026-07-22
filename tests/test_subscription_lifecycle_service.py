from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.models.ai_usage import AIQuotaConfig  # noqa: E402
from common.models.billing import EntitlementLedger  # noqa: E402
from common.services.subscription_lifecycle_service import (  # noqa: E402
    SubscriptionLifecycleError,
    SubscriptionLifecycleService,
)


class FakeSession:
    def __init__(self, scalar_values=None, scalar_rows=None):
        self.scalar_values = list(scalar_values or [])
        self.scalar_rows = list(scalar_rows or [])
        self.added = []

    async def scalar(self, _statement):
        return self.scalar_values.pop(0)

    async def scalars(self, _statement):
        return self.scalar_rows.pop(0)

    def add(self, value):
        self.added.append(value)


def expired_subscription():
    return SimpleNamespace(
        id=11,
        user_id=7,
        plan_id=3,
        plan_code="merchant",
        billing_cycle="monthly",
        status="active",
        account_limit=5,
        monthly_ai_quota=5000,
        ai_unlimited=False,
        feature_snapshot=["ai_reply", "listing_monitor"],
        current_period_start=None,
        current_period_end=None,
        starts_at=datetime(2026, 6, 18, 12, 0),
        expires_at=datetime(2026, 7, 18, 11, 59),
        pending_plan_id=None,
        source_order_id=99,
    )


def pending_subscription():
    """已到期且有待生效降级套餐的订阅：到期应激活 pending 而非降为免费。"""
    return SimpleNamespace(
        id=12,
        user_id=7,
        plan_id=3,
        plan_code="enterprise",
        billing_cycle="monthly",
        status="active",
        account_limit=30,
        monthly_ai_quota=0,
        ai_unlimited=True,
        feature_snapshot=["ai_reply", "api_access"],
        current_period_start=None,
        current_period_end=None,
        starts_at=datetime(2026, 6, 18, 12, 0),
        expires_at=datetime(2026, 7, 18, 11, 59),
        pending_plan_id=2,
        pending_plan_code="standard",
        pending_billing_cycle="monthly",
        pending_duration_months=1,
        pending_account_limit=3,
        pending_monthly_ai_quota=1000,
        pending_ai_unlimited=False,
        pending_feature_snapshot=["ai_reply"],
        pending_source="redemption",
        source_order_id=None,
    )


def free_plan():
    return SimpleNamespace(
        id=1,
        code="free",
        account_limit=1,
        monthly_ai_quota=0,
        feature_flags={"keyword_reply": True, "api_access": False},
    )


class SubscriptionLifecycleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_paid_subscription_downgrades_atomically(self):
        subscription = expired_subscription()
        user = SimpleNamespace(id=7, account_limit=5)
        quota = SimpleNamespace(package_quota=5000, independent_quota=321)
        session = FakeSession([subscription, free_plan(), user, quota])
        now = datetime(2026, 7, 18, 12, 0)

        changed = await SubscriptionLifecycleService(session).expire_user_if_due(7, now)

        self.assertTrue(changed)
        self.assertEqual(subscription.plan_code, "free")
        self.assertEqual(subscription.account_limit, 1)
        self.assertEqual(subscription.monthly_ai_quota, 0)
        self.assertEqual(subscription.feature_snapshot, ["keyword_reply"])
        self.assertIsNone(subscription.expires_at)
        self.assertEqual(user.account_limit, 1)
        self.assertEqual(quota.package_quota, 0)
        self.assertEqual(quota.independent_quota, 321)
        ledgers = [
            item for item in session.added if isinstance(item, EntitlementLedger)
        ]
        self.assertEqual(len(ledgers), 1)
        self.assertEqual(ledgers[0].event_type, "subscription_expired")
        self.assertEqual(
            ledgers[0].idempotency_key,
            "subscription:expired:11:2026-07-18T11:59:00",
        )

    async def test_not_due_subscription_is_unchanged(self):
        session = FakeSession([None])

        changed = await SubscriptionLifecycleService(session).expire_user_if_due(
            7,
            datetime(2026, 7, 18, 12, 0),
        )

        self.assertFalse(changed)
        self.assertEqual(session.added, [])

    async def test_repeated_expiry_check_does_not_duplicate_ledger(self):
        subscription = expired_subscription()
        user = SimpleNamespace(id=7, account_limit=5)
        quota = SimpleNamespace(package_quota=5000, independent_quota=10)
        session = FakeSession([subscription, free_plan(), user, quota, None])
        service = SubscriptionLifecycleService(session)
        now = datetime(2026, 7, 18, 12, 0)

        self.assertTrue(await service.expire_user_if_due(7, now))
        self.assertFalse(await service.expire_user_if_due(7, now))

        ledgers = [
            item for item in session.added if isinstance(item, EntitlementLedger)
        ]
        self.assertEqual(len(ledgers), 1)

    async def test_missing_free_plan_stops_without_mutation(self):
        subscription = expired_subscription()
        session = FakeSession([subscription, None])

        with self.assertRaises(SubscriptionLifecycleError):
            await SubscriptionLifecycleService(session).expire_user_if_due(
                7,
                datetime(2026, 7, 18, 12, 0),
            )

        self.assertEqual(subscription.plan_code, "merchant")
        self.assertEqual(session.added, [])

    async def test_missing_quota_config_is_created(self):
        subscription = expired_subscription()
        user = SimpleNamespace(id=7, account_limit=5)
        session = FakeSession([subscription, free_plan(), user, None])

        await SubscriptionLifecycleService(session).expire_user_if_due(
            7,
            datetime(2026, 7, 18, 12, 0),
        )

        configs = [item for item in session.added if isinstance(item, AIQuotaConfig)]
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].package_quota, 0)
        self.assertEqual(configs[0].independent_quota, 0)

    async def test_expired_subscription_activates_pending_plan(self):
        subscription = pending_subscription()
        user = SimpleNamespace(id=7, account_limit=30)
        quota = SimpleNamespace(package_quota=0, independent_quota=0)
        session = FakeSession([subscription, user, quota])
        now = datetime(2026, 7, 18, 12, 0)

        changed = await SubscriptionLifecycleService(session).expire_user_if_due(
            7, now
        )

        self.assertTrue(changed)
        # 激活待生效降级套餐，而非降为免费
        self.assertEqual(subscription.plan_code, "standard")
        self.assertEqual(subscription.account_limit, 3)
        self.assertEqual(subscription.monthly_ai_quota, 1000)
        self.assertFalse(subscription.ai_unlimited)
        self.assertEqual(subscription.expires_at, datetime(2026, 8, 18, 12, 0))
        self.assertIsNone(subscription.pending_plan_id)
        self.assertEqual(user.account_limit, 3)
        self.assertEqual(quota.package_quota, 1000)
        ledgers = [
            item for item in session.added if isinstance(item, EntitlementLedger)
        ]
        self.assertEqual(len(ledgers), 1)
        self.assertEqual(ledgers[0].event_type, "pending_plan_activated")

    async def test_batch_expiry_processes_locked_rows_once(self):
        first = expired_subscription()
        second = expired_subscription()
        second.id = 12
        second.user_id = 8
        user_one = SimpleNamespace(id=7, account_limit=5)
        user_two = SimpleNamespace(id=8, account_limit=5)
        quota_one = SimpleNamespace(package_quota=5000, independent_quota=10)
        quota_two = SimpleNamespace(package_quota=5000, independent_quota=20)
        session = FakeSession(
            scalar_values=[free_plan(), user_one, quota_one, user_two, quota_two],
            scalar_rows=[[first, second]],
        )

        count = await SubscriptionLifecycleService(session).expire_due_subscriptions(
            datetime(2026, 7, 18, 12, 0),
            limit=100,
        )

        self.assertEqual(count, 2)
        self.assertEqual(first.plan_code, "free")
        self.assertEqual(second.plan_code, "free")
        self.assertEqual(
            len(
                [item for item in session.added if isinstance(item, EntitlementLedger)]
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
