from __future__ import annotations

import io
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.models.billing import AIQuotaPackage, BillingPlan  # noqa: E402
from common.models.redemption import RedemptionBatch, RedemptionCode  # noqa: E402
from common.services.redemption_service import (  # noqa: E402
    RedemptionService,
    compute_code_digest,
    reset_redemption_secret,
    set_redemption_secret,
    _pending_exports,
)

_TEST_SECRET = "a" * 64


def make_plan(code="standard", plan_id=2):
    return BillingPlan(
        id=plan_id,
        code=code,
        name=code,
        account_limit=3,
        monthly_ai_quota=1000,
        ai_unlimited=False,
        feature_flags=["ai_reply"],
        is_free=False,
        enabled=True,
        sort_order=10,
    )


def make_package(code="ai_regular"):
    return AIQuotaPackage(
        id=3,
        code=code,
        name=code,
        base_quota=1200,
        bonus_quota=0,
        ai_unlimited=False,
        amount="12.90",
        validity_days=90,
        enabled=True,
        sort_order=10,
    )


class AddOnlySession:
    """create_batch 只做 add/flush，snapshot 查询返回注入的 plan/package。"""

    def __init__(self, batch=None, plan=None, package=None):
        self._batch = batch
        self._plan = plan
        self._package = package
        self.added: list = []

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def scalar(self, statement):
        entity = self._entity(statement)
        if entity is RedemptionBatch and self._batch is not None:
            b, self._batch = self._batch, None
            return b
        if entity is BillingPlan and self._plan is not None:
            p, self._plan = self._plan, None
            return p
        if entity is AIQuotaPackage and self._package is not None:
            p, self._package = self._package, None
            return p
        return None

    async def scalars(self, _statement):
        return []

    def _entity(self, statement):
        try:
            return statement.column_descriptions[0]["entity"]
        except Exception:
            return None

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = len(self.added) + 1
        self.added.append(value)

    async def flush(self):
        return None


class RedemptionSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        set_redemption_secret(_TEST_SECRET)
        _pending_exports.clear()

    def tearDown(self):
        reset_redemption_secret()
        _pending_exports.clear()

    async def test_codes_are_csprng_unique_and_unpredictable(self):
        session = AddOnlySession(plan=make_plan())
        result = await RedemptionService(session).create_batch(
            admin_id=1,
            product_type="plan",
            product_code="standard",
            cycle_or_validity="monthly",
            count=50,
            expires_at=None,
            now=datetime(2026, 7, 1, 9, 0),
        )
        codes = result.codes
        self.assertEqual(len(codes), 50)
        alphabet = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        for code in codes:
            self.assertEqual(len(code), 24)
            self.assertTrue(set(code) <= alphabet)
        # 全部唯一
        self.assertEqual(len(set(codes)), len(codes))
        # 不含连续编号/时间戳模式：首尾分布不集中在单调序列
        self.assertGreater(len({c[:2] for c in codes}), 1)

    async def test_database_stores_only_digest_and_last4_not_plaintext(self):
        session = AddOnlySession(package=make_package())
        result = await RedemptionService(session).create_batch(
            admin_id=1,
            product_type="ai_quota_package",
            product_code="ai_regular",
            cycle_or_validity=90,
            count=5,
            now=datetime(2026, 7, 1, 9, 0),
        )
        codes = result.codes
        code_rows = [c for c in session.added if isinstance(c, RedemptionCode)]
        self.assertEqual(len(code_rows), 5)
        for row, plain in zip(code_rows, codes):
            self.assertEqual(row.code_digest, compute_code_digest(plain, _TEST_SECRET))
            self.assertEqual(row.code_last4, plain[-4:])
            self.assertEqual(row.status, "unused")
            # 任何持久化字段都不含完整明文
            stored = {k: v for k, v in vars(row).items() if not k.startswith("_")}
            for value in stored.values():
                self.assertNotEqual(value, plain)
                self.assertNotIn(plain, str(value))

    async def test_batch_freezes_entitlement_snapshot(self):
        """生成批次时冻结完整权益快照，不存完整明文兑换码。"""
        session = AddOnlySession(plan=make_plan("standard", plan_id=2))
        await RedemptionService(session).create_batch(
            admin_id=1,
            product_type="plan",
            product_code="standard",
            cycle_or_validity="monthly",
            count=2,
            now=datetime(2026, 7, 1, 9, 0),
        )
        batch = next(v for v in session.added if isinstance(v, RedemptionBatch))
        snapshot = batch.entitlement_snapshot
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["plan_code"], "standard")
        self.assertEqual(snapshot["account_limit"], 3)
        self.assertEqual(snapshot["monthly_ai_quota"], 1000)
        self.assertFalse(snapshot["ai_unlimited"])
        self.assertEqual(snapshot["duration_months"], 1)
        # 快照只保存套餐元数据，不含完整明文兑换码（码只存摘要+尾4，见其余用例）

    async def test_no_full_code_in_logs(self):
        from loguru import logger

        sink = io.StringIO()
        handle = logger.add(sink, level="DEBUG", format="{message}")
        try:
            session = AddOnlySession(plan=make_plan())
            result = await RedemptionService(session).create_batch(
                admin_id=1,
                product_type="plan",
                product_code="standard",
                cycle_or_validity="monthly",
                count=3,
                now=datetime(2026, 7, 1, 9, 0),
            )
            batch = next(v for v in session.added if isinstance(v, RedemptionBatch))
            await RedemptionService(AddOnlySession(batch=batch)).export_batch_once(
                result.batch_id
            )
            log_text = sink.getvalue()
            for plain in result.codes:
                self.assertNotIn(plain, log_text)
        finally:
            logger.remove(handle)

    async def test_export_once_then_unavailable(self):
        session = AddOnlySession(plan=make_plan())
        result = await RedemptionService(session).create_batch(
            admin_id=1,
            product_type="plan",
            product_code="standard",
            cycle_or_validity="monthly",
            count=4,
            now=datetime(2026, 7, 1, 9, 0),
        )
        batch = next(v for v in session.added if isinstance(v, RedemptionBatch))
        batch.exported_at = None

        export_session = AddOnlySession(batch=batch)
        path = await RedemptionService(export_session).export_batch_once(
            result.batch_id
        )
        self.assertTrue(Path(path).exists())
        content = Path(path).read_text(encoding="utf-8").splitlines()
        self.assertEqual(sorted(content), sorted(result.codes))
        # 文件名含 secrets 随机串，不是明文兑换码
        self.assertNotIn(result.codes[0], Path(path).name)

        # 第二次导出：exported_at 已设置 → 拒绝
        batch2 = AddOnlySession(batch=batch)
        batch.exported_at = datetime(2026, 7, 2, 9, 0)
        from common.services.redemption_service import RedemptionError

        with self.assertRaises(RedemptionError):
            await RedemptionService(batch2).export_batch_once(result.batch_id)

        # 进程重启后（清除暂存载荷）即使 exported_at 被回退也无法导出
        _pending_exports.pop(result.batch_id, None)
        batch.exported_at = None
        batch3 = AddOnlySession(batch=batch)
        with self.assertRaises(RedemptionError):
            await RedemptionService(batch3).export_batch_once(result.batch_id)

        RedemptionService.delete_export_file(path)
        self.assertFalse(Path(path).exists())

    async def test_yearly_plan_code_rejected(self):
        from common.services.redemption_service import RedemptionError

        session = AddOnlySession(plan=make_plan())
        with self.assertRaises(RedemptionError):
            await RedemptionService(session).create_batch(
                admin_id=1,
                product_type="plan",
                product_code="standard",
                cycle_or_validity="yearly",
                count=1,
                now=datetime(2026, 7, 1, 9, 0),
            )


if __name__ == "__main__":
    unittest.main()
