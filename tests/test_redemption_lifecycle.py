from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from common.models.billing import BillingPlan  # noqa: E402
from common.models.redemption import RedemptionBatch, RedemptionCode, RedemptionRecord  # noqa: E402
from common.services.redemption_service import (  # noqa: E402
    RedemptionError,
    RedemptionService,
    reset_redemption_secret,
    set_redemption_secret,
)
from redemption_test_helpers import ModelFakeSession  # noqa: E402

_TEST_SECRET = "s" * 64


def make_plan(code="standard", ai_unlimited=False, monthly=1000, limit=3):
    return BillingPlan(
        id=2,
        code=code,
        name=code,
        account_limit=limit,
        monthly_ai_quota=monthly,
        ai_unlimited=ai_unlimited,
        feature_flags=["ai_reply"],
        is_free=False,
        enabled=True,
        sort_order=10,
    )


def make_batch(plan_code="standard", cycle="monthly", months=1, disabled=False, id_=50):
    return RedemptionBatch(
        id=id_,
        batch_no="RB1",
        product_type="plan",
        product_code=plan_code,
        product_name=plan_code,
        billing_cycle=cycle,
        duration_months=months,
        validity_days=None,
        ai_unlimited=False,
        quantity=5,
        generated_count=5,
        used_count=0,
        expires_at=None,
        disabled=disabled,
        created_by=1,
    )


def make_code(batch_id=50, status="unused", disabled=False, expires=None, digest="d"):
    return RedemptionCode(
        id=70,
        batch_id=batch_id,
        code_digest=digest,
        code_last4="AB12",
        status=status,
        used_by=None,
        used_at=None,
        redemption_record_id=None,
        expires_at=expires,
        disabled=disabled,
    )


def make_user(uid=7, limit=1):
    return SimpleNamespace(id=uid, account_limit=limit)


class RedemptionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        set_redemption_secret(_TEST_SECRET)

    def tearDown(self):
        reset_redemption_secret()

    async def test_expired_code_not_consumed(self):
        session = ModelFakeSession()
        code = make_code(expires=datetime(2026, 6, 1, 0, 0))
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionCode, code)
        now = datetime(2026, 7, 1, 9, 0)

        with self.assertRaises(RedemptionError):
            await RedemptionService(session).redeem(7, "any", "idem-1", now)

        self.assertEqual(code.status, "unused")

    async def test_disabled_code_and_batch_rejected(self):
        session = ModelFakeSession()
        code = make_code(disabled=True)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionCode, code)
        with self.assertRaises(RedemptionError):
            await RedemptionService(session).redeem(7, "any", "idem-2")

        session2 = ModelFakeSession()
        session2.queue(RedemptionRecord, None)
        session2.queue(RedemptionCode, make_code())
        session2.queue(RedemptionBatch, make_batch(disabled=True))
        with self.assertRaises(RedemptionError):
            await RedemptionService(session2).redeem(7, "any", "idem-3")

    async def test_concurrent_same_code_only_one_succeeds(self):
        from common.models.ai_usage import AIQuotaConfig
        from common.models.billing import (
            AIQuotaGrant,
            EntitlementLedger,
            UserSubscription,
        )
        from common.models.user import User

        unused = make_code(status="unused")
        used = make_code(status="used")
        session = ModelFakeSession()
        session.queue(RedemptionRecord, None, None)
        session.queue(RedemptionCode, unused, used)
        session.queue(RedemptionBatch, make_batch())
        session.queue(BillingPlan, make_plan())
        session.queue(UserSubscription, None)
        session.queue(User, make_user())
        session.queue(AIQuotaConfig, None)
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)

        now = datetime(2026, 7, 1, 9, 0)
        results = await asyncio.gather(
            RedemptionService(session).redeem(7, "c1", "idem-a", now),
            RedemptionService(session).redeem(7, "c1", "idem-b", now),
            return_exceptions=True,
        )

        succeeded = [r for r in results if not isinstance(r, Exception)]
        failed = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(len(failed), 1)
        self.assertIsInstance(failed[0], RedemptionError)

    async def test_request_idempotency_returns_existing(self):
        from common.models.ai_usage import AIQuotaConfig
        from common.models.billing import (
            AIQuotaGrant,
            EntitlementLedger,
            UserSubscription,
        )
        from common.models.user import User

        session = ModelFakeSession()
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionCode, make_code())
        session.queue(RedemptionBatch, make_batch())
        session.queue(BillingPlan, make_plan())
        session.queue(UserSubscription, None)
        session.queue(User, make_user())
        session.queue(AIQuotaConfig, None)
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)

        now = datetime(2026, 7, 1, 9, 0)
        first = await RedemptionService(session).redeem(7, "c1", "idem-same", now)
        self.assertFalse(first.duplicate)

        records = [r for r in session.added if isinstance(r, RedemptionRecord)]
        self.assertEqual(len(records), 1)
        existing = records[0]

        # 重放：相同 idempotency_key 直接返回已有记录，不再查询兑换码
        replay_session = ModelFakeSession()
        replay_session.queue(RedemptionRecord, existing)
        replayed = await RedemptionService(replay_session).redeem(
            7, "c1", "idem-same", now
        )
        self.assertTrue(replayed.duplicate)
        self.assertEqual(replayed.record_id, existing.id)
        self.assertEqual(replayed.grant_id, existing.grant_id)

    async def test_entitlement_failure_rolls_back_code_not_consumed(self):
        from common.models.billing import UserSubscription
        from common.models.user import User

        session = ModelFakeSession()
        code = make_code(status="unused")
        batch = make_batch()
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionCode, code)
        session.queue(RedemptionBatch, batch)
        session.queue(BillingPlan, make_plan())
        session.queue(UserSubscription, None)
        session.queue(User, None)  # 用户不存在 → 发放失败

        now = datetime(2026, 7, 1, 9, 0)
        with self.assertRaises(RedemptionError):
            await RedemptionService(session).redeem(7, "c1", "idem-fail", now)

        # 兑换码未被消耗，不形成部分权益
        self.assertEqual(code.status, "unused")
        self.assertIsNone(code.used_by)
        self.assertEqual(batch.used_count, 0)


if __name__ == "__main__":
    unittest.main()
