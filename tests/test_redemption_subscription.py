from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from common.models.billing import (  # noqa: E402
    AIQuotaGrant,
    EntitlementLedger,
    UserSubscription,
)
from common.models.ai_usage import AIQuotaConfig  # noqa: E402
from common.models.user import User  # noqa: E402
from common.services.entitlement_grant_service import (  # noqa: E402
    EntitlementGrantError,
    EntitlementGrantService,
)
from redemption_test_helpers import ModelFakeSession  # noqa: E402

NOW = datetime(2026, 7, 18, 9, 0)


def std_snapshot(cycle="monthly", months=1):
    return {
        "plan_id": 2,
        "plan_code": "standard",
        "billing_cycle": cycle,
        "duration_months": months,
        "account_limit": 3,
        "monthly_ai_quota": 1000,
        "ai_unlimited": False,
        "feature_flags": ["ai_reply"],
        "sort_order": 10,
    }


def mch_snapshot(cycle="monthly", months=1):
    return {
        "plan_id": 3,
        "plan_code": "merchant",
        "billing_cycle": cycle,
        "duration_months": months,
        "account_limit": 8,
        "monthly_ai_quota": 5000,
        "ai_unlimited": False,
        "feature_flags": ["ai_reply", "listing_monitor"],
        "sort_order": 20,
    }


def ent_snapshot(cycle="monthly", months=1):
    return {
        "plan_id": 4,
        "plan_code": "enterprise",
        "billing_cycle": cycle,
        "duration_months": months,
        "account_limit": 30,
        "monthly_ai_quota": 0,
        "ai_unlimited": True,
        "feature_flags": ["ai_reply", "api_access"],
        "sort_order": 30,
    }


def existing_sub(
    plan_code="standard",
    plan_id=2,
    monthly=1000,
    limit=3,
    unlimited=False,
    expires=None,
    sub_id=11,
    cycle="monthly",
):
    return UserSubscription(
        id=sub_id,
        user_id=7,
        plan_id=plan_id,
        plan_code=plan_code,
        billing_cycle=cycle,
        status="active",
        account_limit=limit,
        monthly_ai_quota=monthly,
        ai_unlimited=unlimited,
        feature_snapshot=["ai_reply"],
        current_period_start=None,
        current_period_end=None,
        starts_at=NOW,
        expires_at=expires,
        pending_plan_id=None,
    )


def user_obj(uid=7, limit=3):
    return SimpleNamespace(id=uid, account_limit=limit)


def config_obj(pkg=1000, indep=0):
    return SimpleNamespace(package_quota=pkg, independent_quota=indep)


class EntitlementPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_monthly_new_subscription_grants_current_month(self):
        session = ModelFakeSession()
        session.queue(UserSubscription, None)
        session.queue(User, user_obj())
        session.queue(AIQuotaConfig, None)
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)

        result = await EntitlementGrantService(session).grant_plan(
            user_id=7,
            plan_snapshot=std_snapshot(),
            billing_cycle="monthly",
            duration_months=1,
            idempotency_key="redemption:1",
            source={"kind": "redemption", "event_type": "redemption_grant"},
            now=NOW,
        )
        sub = next(v for v in session.added if isinstance(v, UserSubscription))
        self.assertEqual(sub.plan_code, "standard")
        self.assertEqual(sub.expires_at, datetime(2026, 8, 18, 9, 0))
        self.assertFalse(sub.ai_unlimited)
        grants = [v for v in session.added if isinstance(v, AIQuotaGrant)]
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].total_quota, 1000)
        self.assertEqual(result.action, "new")

    async def test_quarterly_extends_three_months(self):
        from common.models.ai_usage import AIQuotaConfig
        from common.models.user import User

        session = ModelFakeSession()
        session.queue(UserSubscription, None)
        session.queue(User, user_obj())
        session.queue(AIQuotaConfig, None)
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)

        await EntitlementGrantService(session).grant_plan(
            user_id=7,
            plan_snapshot=std_snapshot("quarterly", 3),
            billing_cycle="quarterly",
            duration_months=3,
            idempotency_key="redemption:2",
            source={"kind": "redemption"},
            now=NOW,
        )
        sub = next(v for v in session.added if isinstance(v, UserSubscription))
        self.assertEqual(sub.expires_at, datetime(2026, 10, 18, 9, 0))
        self.assertEqual(sub.billing_cycle, "quarterly")

    async def test_same_plan_renewal_extends_and_skips_same_month_grant(self):
        from common.models.ai_usage import AIQuotaConfig
        from common.models.user import User

        old_expiry = datetime(2026, 7, 28, 9, 0)
        sub = existing_sub(expires=old_expiry, sub_id=11)
        monthly_key = "sub-monthly:7:11:2026-07-01"
        existing_grant = AIQuotaGrant(
            id=999,
            user_id=7,
            grant_type="subscription",
            idempotency_key=monthly_key,
            total_quota=1000,
            remaining_quota=1000,
            ai_unlimited=False,
            starts_at=NOW,
            expires_at=None,
            status="active",
        )
        session = ModelFakeSession()
        session.queue(UserSubscription, sub)
        session.queue(User, user_obj())
        session.queue(AIQuotaConfig, config_obj(pkg=1000))
        session.queue(AIQuotaGrant, existing_grant)
        session.queue(EntitlementLedger, None)

        result = await EntitlementGrantService(session).grant_plan(
            user_id=7,
            plan_snapshot=std_snapshot(),
            billing_cycle="monthly",
            duration_months=1,
            idempotency_key="redemption:3",
            source={"kind": "redemption"},
            now=NOW,
        )
        # 到期日从当前到期日顺延 1 个月
        self.assertEqual(sub.expires_at, datetime(2026, 8, 28, 9, 0))
        # 同一自然月不重复发放当月套餐额度：未新增 grant
        self.assertEqual([v for v in session.added if isinstance(v, AIQuotaGrant)], [])
        self.assertEqual(result.grant_id, 999)
        self.assertEqual(result.action, "renew")

    async def test_upgrade_immediate_no_prorate(self):
        from common.models.ai_usage import AIQuotaConfig
        from common.models.user import User

        old_expiry = datetime(2026, 8, 5, 9, 0)  # 仍未到期
        sub = existing_sub(
            plan_code="standard",
            plan_id=2,
            monthly=1000,
            limit=3,
            expires=old_expiry,
            sub_id=11,
        )
        session = ModelFakeSession()
        session.queue(UserSubscription, sub)
        session.queue(User, user_obj(limit=3))
        session.queue(AIQuotaConfig, config_obj(pkg=1000))
        session.queue(AIQuotaGrant, None)  # 升级到商家版，当月新 grant
        session.queue(EntitlementLedger, None)

        result = await EntitlementGrantService(session).grant_plan(
            user_id=7,
            plan_snapshot=mch_snapshot(),
            billing_cycle="monthly",
            duration_months=1,
            idempotency_key="redemption:4",
            source={"kind": "redemption"},
            now=NOW,
        )
        self.assertEqual(sub.plan_code, "merchant")
        self.assertEqual(sub.account_limit, 8)
        self.assertEqual(sub.monthly_ai_quota, 5000)
        # max(当前到期日, now) + 新套餐时长，不补差价
        self.assertEqual(sub.expires_at, datetime(2026, 9, 5, 9, 0))
        self.assertIsNone(sub.pending_plan_id)
        self.assertEqual(result.action, "upgrade")
        # 升级后发当月新额度
        grants = [v for v in session.added if isinstance(v, AIQuotaGrant)]
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].total_quota, 5000)

    async def test_downgrade_pending_and_second_cross_plan_rejected(self):
        from common.models.user import User

        sub = existing_sub(
            plan_code="enterprise",
            plan_id=4,
            monthly=0,
            limit=30,
            unlimited=True,
            expires=datetime(2026, 9, 1, 9, 0),
            sub_id=11,
        )
        session = ModelFakeSession()
        session.queue(UserSubscription, sub)
        session.queue(User, user_obj(limit=30))
        session.queue(EntitlementLedger, None)

        result = await EntitlementGrantService(session).grant_plan(
            user_id=7,
            plan_snapshot=std_snapshot(),
            billing_cycle="monthly",
            duration_months=1,
            idempotency_key="redemption:5",
            source={"kind": "redemption"},
            now=NOW,
        )
        # 当前套餐不变，写入待生效降级
        self.assertEqual(result.action, "pending_downgrade")
        self.assertEqual(sub.plan_code, "enterprise")
        self.assertEqual(sub.pending_plan_code, "standard")
        self.assertEqual(sub.pending_account_limit, 3)

        # 已有待生效降级时，再兑换跨套餐码必须拒绝
        session2 = ModelFakeSession()
        session2.queue(UserSubscription, sub)  # pending_plan_id 已设
        session2.queue(User, user_obj(limit=30))
        session2.queue(EntitlementLedger, None)
        with self.assertRaises(EntitlementGrantError):
            await EntitlementGrantService(session2).grant_plan(
                user_id=7,
                plan_snapshot=mch_snapshot(),
                billing_cycle="monthly",
                duration_months=1,
                idempotency_key="redemption:6",
                source={"kind": "redemption"},
                now=NOW,
            )

    async def test_enterprise_renewal_only_extends_unlimited_validity(self):
        from common.models.ai_usage import AIQuotaConfig
        from common.models.user import User

        old_expiry = datetime(2026, 8, 1, 9, 0)
        sub = existing_sub(
            plan_code="enterprise",
            plan_id=4,
            monthly=0,
            limit=30,
            unlimited=True,
            expires=old_expiry,
            sub_id=11,
            cycle="monthly",
        )
        session = ModelFakeSession()
        session.queue(UserSubscription, sub)
        session.queue(User, user_obj(limit=30))
        session.queue(AIQuotaConfig, config_obj(pkg=0))
        session.queue(EntitlementLedger, None)

        result = await EntitlementGrantService(session).grant_plan(
            user_id=7,
            plan_snapshot=ent_snapshot(),
            billing_cycle="monthly",
            duration_months=1,
            idempotency_key="redemption:7",
            source={"kind": "redemption"},
            now=NOW,
        )
        # 续费只延长无限有效期，不创建次数额度 grant
        self.assertEqual(sub.expires_at, datetime(2026, 9, 1, 9, 0))
        self.assertTrue(sub.ai_unlimited)
        self.assertEqual([v for v in session.added if isinstance(v, AIQuotaGrant)], [])
        self.assertEqual(result.action, "renew")
        self.assertTrue(result.ai_unlimited)


if __name__ == "__main__":
    unittest.main()
