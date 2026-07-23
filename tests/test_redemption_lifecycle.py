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

from common.models.billing import (  # noqa: E402
    AIQuotaGrant,
    EntitlementLedger,
    UserSubscription,
)
from common.models.redemption import RedemptionBatch, RedemptionCode, RedemptionRecord  # noqa: E402
from common.services.redemption_service import (  # noqa: E402
    RedemptionError,
    RedemptionService,
    reset_redemption_secret,
    set_redemption_secret,
)
from redemption_test_helpers import ModelFakeSession  # noqa: E402

_TEST_SECRET = "s" * 64


def std_snapshot(code="standard", plan_id=2, limit=3, monthly=1000, unlimited=False):
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


def make_batch(
    plan_code="standard",
    cycle="monthly",
    months=1,
    disabled=False,
    id_=50,
    snapshot=None,
):
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
        entitlement_snapshot=snapshot or std_snapshot(code=plan_code),
        quantity=5,
        generated_count=5,
        used_count=0,
        expires_at=None,
        disabled=disabled,
        created_by=1,
    )


def make_code(
    batch_id=50,
    status="unused",
    disabled=False,
    expires=None,
    digest="d",
    id_=70,
):
    return RedemptionCode(
        id=id_,
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
        # 新顺序：先锁兑换码，再幂等查询
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        now = datetime(2026, 7, 1, 9, 0)

        with self.assertRaises(RedemptionError):
            await RedemptionService(session).redeem(7, "any", "idem-1", now)

        self.assertEqual(code.status, "unused")

    async def test_disabled_code_and_batch_rejected(self):
        session = ModelFakeSession()
        code = make_code(disabled=True)
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        with self.assertRaises(RedemptionError):
            await RedemptionService(session).redeem(7, "any", "idem-2")

        session2 = ModelFakeSession()
        session2.queue(RedemptionCode, make_code())
        session2.queue(RedemptionRecord, None)
        session2.queue(RedemptionBatch, make_batch(disabled=True))
        with self.assertRaises(RedemptionError):
            await RedemptionService(session2).redeem(7, "any", "idem-3")

    async def test_concurrent_same_code_only_one_succeeds(self):
        from common.models.ai_usage import AIQuotaConfig
        from common.models.user import User

        unused = make_code(status="unused")
        used = make_code(status="used")
        session = ModelFakeSession()
        session.queue(RedemptionCode, unused, used)
        session.queue(RedemptionRecord, None, None)
        session.queue(RedemptionBatch, make_batch())
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
        from common.models.user import User

        code = make_code()
        session = ModelFakeSession()
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionBatch, make_batch())
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

        # 重放：相同用户+相同幂等键+相同兑换码 → 返回已有记录，不再发放
        replay_session = ModelFakeSession()
        replay_session.queue(RedemptionCode, code)  # 同一枚举码（已被标记 used）
        replay_session.queue(RedemptionRecord, existing)
        replayed = await RedemptionService(replay_session).redeem(
            7, "c1", "idem-same", now
        )
        self.assertTrue(replayed.duplicate)
        self.assertEqual(replayed.record_id, existing.id)
        self.assertEqual(replayed.grant_id, existing.grant_id)

    async def test_same_user_same_key_different_code_rejected(self):
        """同一用户用同一幂等键提交不同兑换码：明确拒绝，不返回旧成功结果。"""
        from common.models.ai_usage import AIQuotaConfig
        from common.models.user import User

        code_a = make_code(id_=70, digest="da")
        session = ModelFakeSession()
        session.queue(RedemptionCode, code_a)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionBatch, make_batch(id_=50))
        session.queue(UserSubscription, None)
        session.queue(User, make_user())
        session.queue(AIQuotaConfig, None)
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)

        now = datetime(2026, 7, 1, 9, 0)
        first = await RedemptionService(session).redeem(7, "ca", "idem-x", now)
        self.assertFalse(first.duplicate)
        existing = next(r for r in session.added if isinstance(r, RedemptionRecord))
        self.assertEqual(existing.code_id, code_a.id)

        # 同一用户、同一幂等键、不同兑换码（code_b id=71）→ 拒绝
        code_b = make_code(id_=71, digest="db")
        session2 = ModelFakeSession()
        session2.queue(RedemptionCode, code_b)
        session2.queue(RedemptionRecord, existing)
        with self.assertRaises(RedemptionError):
            await RedemptionService(session2).redeem(7, "cb", "idem-x", now)

        # 旧兑换码不被二次消耗
        self.assertEqual(code_b.status, "unused")

    async def test_entitlement_failure_rolls_back_code_not_consumed(self):
        from common.models.user import User

        session = ModelFakeSession()
        code = make_code(status="unused")
        batch = make_batch()
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionBatch, batch)
        session.queue(UserSubscription, None)
        session.queue(User, None)  # 用户不存在 → 发放失败

        now = datetime(2026, 7, 1, 9, 0)
        with self.assertRaises(RedemptionError):
            await RedemptionService(session).redeem(7, "c1", "idem-fail", now)

        # 兑换码未被消耗，不形成部分权益
        self.assertEqual(code.status, "unused")
        self.assertIsNone(code.used_by)
        self.assertEqual(batch.used_count, 0)

    async def test_audit_ledger_records_code_and_batch_source(self):
        """权益流水 details 保留 code_id / batch_id，可追溯到兑换码与批次。"""
        from common.models.ai_usage import AIQuotaConfig
        from common.models.user import User

        code = make_code(id_=70)
        batch = make_batch(id_=50)
        session = ModelFakeSession()
        session.queue(RedemptionCode, code)
        session.queue(RedemptionRecord, None)
        session.queue(RedemptionBatch, batch)
        session.queue(UserSubscription, None)
        session.queue(User, make_user())
        session.queue(AIQuotaConfig, None)
        session.queue(AIQuotaGrant, None)
        session.queue(EntitlementLedger, None)

        now = datetime(2026, 7, 1, 9, 0)
        plaintext = "auditcode-XYZ"  # 含非 hex 字符，避免与摘要 hex 误匹配
        result = await RedemptionService(session).redeem(
            7, plaintext, "idem-audit", now
        )

        ledger = next(r for r in session.added if isinstance(r, EntitlementLedger))
        self.assertEqual(ledger.details.get("code_id"), code.id)
        self.assertEqual(ledger.details.get("batch_id"), batch.id)

        record = next(r for r in session.added if isinstance(r, RedemptionRecord))
        self.assertEqual(record.code_id, code.id)
        self.assertEqual(record.batch_id, batch.id)
        self.assertEqual(record.grant_id, result.grant_id)
        self.assertEqual(record.ledger_id, ledger.id)
        # 审计记录不含完整明文兑换码（code_id 为整数引用，不存明文/摘要）
        for value in vars(record).values():
            self.assertNotIn(plaintext, str(value))


if __name__ == "__main__":
    unittest.main()
