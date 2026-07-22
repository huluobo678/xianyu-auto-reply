from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from common.models.ai_usage import (  # noqa: E402
    AIAccountMonthlyUsage,
    AIQuotaConfig,
    AIUsageRequest,
    AIUserMonthlyUsage,
)
from common.models.billing import (  # noqa: E402
    AIQuotaGrant,
    EntitlementLedger,
    UserSubscription,
)
from common.models.user import User  # noqa: E402
from common.services.ai_usage_service import AIUsageService  # noqa: E402
from common.services.entitlement_grant_service import (  # noqa: E402
    EntitlementGrantService,
)
from redemption_test_helpers import ModelFakeSession  # noqa: E402

NOW = datetime(2026, 7, 18, 9, 0)


def pkg_snapshot(code="ai_regular", base=1200, bonus=0, validity=90, unlimited=False):
    return {
        "package_code": code,
        "base_quota": base,
        "bonus_quota": bonus,
        "total_quota": base + bonus,
        "validity_days": validity,
        "ai_unlimited": unlimited,
    }


def user_obj(uid=7, limit=1):
    return SimpleNamespace(id=uid, account_limit=limit)


def config_obj(pkg=0, indep=0):
    return SimpleNamespace(package_quota=pkg, independent_quota=indep)


class QuotaPackageTests(unittest.IsolatedAsyncioTestCase):
    async def _grant(self, snapshot):
        session = ModelFakeSession()
        session.queue(User, user_obj())
        session.queue(AIQuotaConfig, config_obj())
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)
        await EntitlementGrantService(session).grant_quota_package(
            user_id=7,
            package_snapshot=snapshot,
            idempotency_key="redemption:pkg",
            source={"kind": "redemption", "event_type": "redemption_grant"},
            now=NOW,
        )
        return session

    async def test_regular_package_grant(self):
        session = await self._grant(pkg_snapshot("ai_regular", 1200, 0, 90, False))
        grant = next(v for v in session.added if isinstance(v, AIQuotaGrant))
        self.assertEqual(grant.grant_type, "quota_package")
        self.assertEqual(grant.total_quota, 1200)
        self.assertEqual(grant.remaining_quota, 1200)
        self.assertFalse(grant.ai_unlimited)
        self.assertEqual(grant.expires_at, NOW + timedelta(days=90))

    async def test_merchant_package_grant(self):
        session = await self._grant(pkg_snapshot("ai_merchant", 6000, 0, 180, False))
        grant = next(v for v in session.added if isinstance(v, AIQuotaGrant))
        self.assertEqual(grant.total_quota, 6000)
        self.assertEqual(grant.expires_at, NOW + timedelta(days=180))

    async def test_unlimited_package_grant(self):
        session = await self._grant(pkg_snapshot("ai_unlimited", 0, 0, 30, True))
        grant = next(v for v in session.added if isinstance(v, AIQuotaGrant))
        # 无限包：ai_unlimited=1，不创建次数额度
        self.assertTrue(grant.ai_unlimited)
        self.assertEqual(grant.total_quota, 0)
        self.assertEqual(grant.remaining_quota, 0)
        self.assertEqual(grant.expires_at, NOW + timedelta(days=30))

    async def test_enterprise_and_unlimited_package_overlap_no_count_grant(self):
        # 企业版订阅 + 兑换无限包：两者均无限，不重复产生次数额度
        unlimited_grant_session = ModelFakeSession()
        unlimited_grant_session.queue(User, user_obj())
        unlimited_grant_session.queue(AIQuotaConfig, config_obj())
        unlimited_grant_session.queue(AIQuotaGrant, None)
        unlimited_grant_session.queue(EntitlementLedger, None)
        await EntitlementGrantService(unlimited_grant_session).grant_quota_package(
            user_id=7,
            package_snapshot=pkg_snapshot("ai_unlimited", 0, 0, 30, True),
            idempotency_key="redemption:overlap",
            source={"kind": "redemption"},
            now=NOW,
        )
        grants = [
            v for v in unlimited_grant_session.added if isinstance(v, AIQuotaGrant)
        ]
        self.assertEqual(len(grants), 1)
        self.assertTrue(grants[0].ai_unlimited)
        self.assertEqual(grants[0].total_quota, 0)  # 无次数额度


class UnlimitedDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_enterprise_subscription_detected_as_unlimited(self):
        sub = UserSubscription(
            id=11,
            user_id=7,
            plan_id=4,
            plan_code="enterprise",
            status="active",
            account_limit=30,
            monthly_ai_quota=0,
            ai_unlimited=True,
            feature_snapshot=["ai_reply"],
            starts_at=NOW,
            expires_at=NOW + timedelta(days=30),
        )
        session = ModelFakeSession()
        session.queue(AIQuotaGrant)  # 空：无无限包 grant
        session.queue(UserSubscription, sub)
        self.assertTrue(await AIUsageService._has_active_unlimited(session, 7, NOW))

    async def test_unlimited_package_grant_detected_as_unlimited(self):
        grant = AIQuotaGrant(
            id=1,
            user_id=7,
            grant_type="quota_package",
            ai_unlimited=True,
            total_quota=0,
            remaining_quota=0,
            status="active",
            starts_at=NOW,
            expires_at=NOW + timedelta(days=30),
        )
        session = ModelFakeSession()
        session.queue(AIQuotaGrant, grant)
        self.assertTrue(await AIUsageService._has_active_unlimited(session, 7, NOW))

    async def test_free_user_not_unlimited(self):
        session = ModelFakeSession()
        # 无无限 grant、无无限订阅
        self.assertFalse(await AIUsageService._has_active_unlimited(session, 7, NOW))


class UnlimitedRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_unlimited_user_commit_records_replies_tokens_and_cost(self):
        request = AIUsageRequest(
            id=1,
            idempotency_key="k",
            user_id=7,
            account_pk=8,
            account_id="acct",
            period_start=date(2026, 7, 1),
            status="reserved",
            estimated_cost="0.125",
        )
        user_usage = SimpleNamespace(
            effective_replies=0,
            reserved_replies=1,
            estimated_cost=0,
            period_start=date(2026, 7, 1),
            warned_80_at=None,
            warned_100_at=None,
        )
        account_usage = SimpleNamespace(
            effective_replies=0,
            reserved_replies=1,
            estimated_cost=0,
        )
        unlimited_grant = AIQuotaGrant(
            id=1,
            user_id=7,
            ai_unlimited=True,
            status="active",
            starts_at=NOW,
            expires_at=NOW + timedelta(days=30),
        )

        session = ModelFakeSession()
        session.queue(AIUsageRequest, request)
        session.queue(AIUserMonthlyUsage, user_usage)
        session.queue(AIAccountMonthlyUsage, account_usage)
        # _mark_thresholds → _has_active_unlimited 命中无限 grant 即返回
        session.queue(AIQuotaGrant, unlimited_grant)

        committed = await AIUsageService.commit(session, 1, auto_reply_log_id=55)
        self.assertTrue(committed)
        self.assertEqual(request.status, "committed")
        self.assertEqual(request.auto_reply_log_id, 55)
        self.assertIsNotNone(request.committed_at)
        # 有效回复、成本仍记录
        self.assertEqual(user_usage.effective_replies, 1)
        self.assertEqual(account_usage.effective_replies, 1)
        self.assertGreater(float(user_usage.estimated_cost), 0)
        # 无限用户跳过 80%/100% 阈值告警
        self.assertIsNone(user_usage.warned_80_at)
        self.assertIsNone(user_usage.warned_100_at)


if __name__ == "__main__":
    unittest.main()
