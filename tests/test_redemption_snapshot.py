from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from common.models.ai_usage import AIQuotaConfig  # noqa: E402
from common.models.billing import (  # noqa: E402
    AIQuotaGrant,
    EntitlementLedger,
    UserSubscription,
)
from common.models.redemption import RedemptionBatch, RedemptionCode, RedemptionRecord  # noqa: E402
from common.models.user import User  # noqa: E402
from common.services.redemption_service import (  # noqa: E402
    RedemptionError,
    RedemptionService,
    reset_redemption_secret,
    set_redemption_secret,
)
from redemption_test_helpers import ModelFakeSession  # noqa: E402

_TEST_SECRET = "s" * 64
NOW = datetime(2026, 7, 1, 9, 0)


def plan_snapshot(monthly=1000, limit=3, code="standard", plan_id=2, unlimited=False):
    return {
        "plan_id": plan_id,
        "plan_code": code,
        "plan_name": code,
        "billing_cycle": "monthly",
        "duration_months": 1,
        "account_limit": limit,
        "monthly_ai_quota": monthly,
        "ai_unlimited": unlimited,
        "feature_flags": ["ai_reply"],
        "sort_order": 10,
    }


def make_batch(snapshot):
    return RedemptionBatch(
        id=50,
        batch_no="RB1",
        product_type="plan",
        product_code="standard",
        product_name="standard",
        billing_cycle="monthly",
        duration_months=1,
        validity_days=None,
        ai_unlimited=False,
        entitlement_snapshot=snapshot,
        quantity=5,
        generated_count=5,
        used_count=0,
        expires_at=None,
        disabled=False,
        created_by=1,
    )


def make_code(id_=70):
    return RedemptionCode(
        id=id_,
        batch_id=50,
        code_digest="d",
        code_last4="AB12",
        status="unused",
        used_by=None,
        used_at=None,
        redemption_record_id=None,
        expires_at=None,
        disabled=False,
    )


def make_user(uid=7, limit=1):
    return SimpleNamespace(id=uid, account_limit=limit)


class SnapshotImmutabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        set_redemption_secret(_TEST_SECRET)

    def tearDown(self):
        reset_redemption_secret()

    async def test_redeem_uses_frozen_snapshot_not_current_catalog(self):
        """兑换只依赖批次冻结快照，不再查询当前套餐目录。"""
        snapshot = plan_snapshot(monthly=1000, limit=3)
        code = make_code()
        batch = make_batch(snapshot)
        session = ModelFakeSession()
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionBatch, batch)
        session.queue(UserSubscription, None)
        session.queue(User, make_user())
        session.queue(AIQuotaConfig, None)
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)
        # 注意：不排队 BillingPlan —— 若 redeem 重新读目录会因查询返回 None 而失败。
        # 通过即证明只使用快照。

        result = await RedemptionService(session).redeem(7, "c1", "idem-snap", NOW)
        self.assertFalse(result.duplicate)

        sub = next(v for v in session.added if isinstance(v, UserSubscription))
        self.assertEqual(sub.monthly_ai_quota, 1000)
        self.assertEqual(sub.account_limit, 3)
        self.assertEqual(sub.plan_code, "standard")

    async def test_catalog_change_after_batch_does_not_affect_redeem(self):
        """生成批次后修改套餐目录（快照值不同），兑换仍发放原始权益。"""
        # 批次快照为商家版额度 5000；模拟目录已被改成 standard(1000)
        snapshot = plan_snapshot(monthly=5000, limit=8, code="merchant", plan_id=3)
        code = make_code()
        batch = make_batch(snapshot)
        batch.product_code = "merchant"
        session = ModelFakeSession()
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionBatch, batch)
        session.queue(UserSubscription, None)
        session.queue(User, make_user(limit=8))
        session.queue(AIQuotaConfig, None)
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)

        await RedemptionService(session).redeem(7, "c1", "idem-catalog", NOW)
        sub = next(v for v in session.added if isinstance(v, UserSubscription))
        # 仍按快照发放 5000，而非目录被改后的值
        self.assertEqual(sub.monthly_ai_quota, 5000)
        self.assertEqual(sub.account_limit, 8)
        self.assertEqual(sub.plan_code, "merchant")

    async def test_missing_snapshot_rejected_and_code_not_consumed(self):
        """快照缺失：失败关闭，不消耗兑换码、不发放部分权益。"""
        code = make_code()
        batch = make_batch(None)
        session = ModelFakeSession()
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionBatch, batch)

        with self.assertRaises(RedemptionError):
            await RedemptionService(session).redeem(7, "c1", "idem-missing", NOW)

        self.assertEqual(code.status, "unused")
        self.assertEqual([v for v in session.added if isinstance(v, AIQuotaGrant)], [])
        self.assertEqual(
            [v for v in session.added if isinstance(v, EntitlementLedger)], []
        )
        self.assertEqual(
            [v for v in session.added if isinstance(v, RedemptionRecord)], []
        )

    async def test_corrupted_snapshot_rejected_and_code_not_consumed(self):
        """快照结构损坏（缺必要字段）：失败关闭，不消耗兑换码。"""
        code = make_code()
        batch = make_batch({"plan_code": "standard"})  # 缺 account_limit 等字段
        session = ModelFakeSession()
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionBatch, batch)

        with self.assertRaises(RedemptionError):
            await RedemptionService(session).redeem(7, "c1", "idem-corrupt", NOW)

        self.assertEqual(code.status, "unused")


if __name__ == "__main__":
    unittest.main()
