from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend-web"))

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.services import redemption_secret_service as rss  # noqa: E402
from common.services import redemption_service as rs  # noqa: E402
from common.services.redemption_service import (  # noqa: E402
    REDEMPTION_HMAC_SETTING_KEY,
    reset_redemption_secret,
)


class FakeSecretSession:
    """模拟 SystemSetting 读写与事务的会话，用于测试密钥失败关闭策略。"""

    def __init__(
        self,
        *,
        read_results=None,
        shared_db=None,
        fail_read=False,
        fail_flush=False,
        fail_commit=False,
        integrity_on_flush=False,
    ):
        self._read_results = list(read_results or [])
        self.shared_db = shared_db if shared_db is not None else {}
        self.fail_read = fail_read
        self.fail_flush = fail_flush
        self.fail_commit = fail_commit
        self.integrity_on_flush = integrity_on_flush
        self.added = []
        self.rolled_back = False
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _stmt):
        if self.fail_read:
            raise RuntimeError("database read failed")
        if self._read_results:
            val = self._read_results.pop(0)
        else:
            val = self.shared_db.get(REDEMPTION_HMAC_SETTING_KEY)
        return SimpleNamespace(scalar_one_or_none=lambda v=val: v)

    def add(self, record):
        self.added.append(record)

    async def flush(self):
        if self.integrity_on_flush:
            raise IntegrityError("dup", params=None, orig=Exception("dup"))
        if self.fail_flush:
            raise RuntimeError("flush failed")

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.committed = True
        for rec in self.added:
            self.shared_db[rec.key] = rec.value

    async def rollback(self):
        self.rolled_back = True


class FailClosedSecretTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_redemption_secret()
        self._orig_maker = rss.async_session_maker

    def tearDown(self):
        rss.async_session_maker = self._orig_maker
        reset_redemption_secret()

    def _patch(self, session_factory):
        rss.async_session_maker = session_factory

    async def test_db_read_failure_does_not_use_temp_secret(self):
        # 数据库读取失败：失败关闭，不回退临时随机密钥
        self._patch(lambda: FakeSecretSession(fail_read=True))
        with self.assertRaises(rss.RedemptionSecretUnavailableError):
            await rss.ensure_redemption_secret()
        # 进程内无临时密钥被注入
        self.assertIsNone(rs._injected_secret)

    async def test_db_write_failure_does_not_continue(self):
        # 数据库写入失败：失败关闭，不继续运行
        self._patch(
            lambda: FakeSecretSession(
                read_results=[None], fail_commit=True, shared_db={}
            )
        )
        with self.assertRaises(rss.RedemptionSecretUnavailableError):
            await rss.ensure_redemption_secret()
        self.assertIsNone(rs._injected_secret)

    async def test_first_init_persists_then_uses_secret(self):
        shared_db = {}
        self._patch(lambda: FakeSecretSession(read_results=[None], shared_db=shared_db))
        secret = await rss.ensure_redemption_secret()
        self.assertEqual(len(secret), rss._REDEMPTION_SECRET_LENGTH)
        # 持久化成功后才注入使用
        self.assertEqual(rs._injected_secret, secret)
        self.assertIn(REDEMPTION_HMAC_SETTING_KEY, shared_db)
        self.assertEqual(shared_db[REDEMPTION_HMAC_SETTING_KEY], secret)

    async def test_concurrent_init_uses_winner_persisted_secret(self):
        # 进程 A 抢先持久化密钥
        shared_db = {}
        self._patch(lambda: FakeSecretSession(read_results=[None], shared_db=shared_db))
        secret_a = await rss.ensure_redemption_secret()
        reset_redemption_secret()

        # 进程 B 并发：首次读为空，插入时唯一键冲突 → 回滚 → 读取获胜进程密钥
        winner_session = FakeSecretSession(
            read_results=[None, secret_a],
            shared_db=shared_db,
            integrity_on_flush=True,
        )
        self._patch(lambda: winner_session)
        secret_b = await rss.ensure_redemption_secret()
        # 最终使用获胜进程持久化的同一密钥，而非各自临时密钥
        self.assertEqual(secret_b, secret_a)
        self.assertEqual(rs._injected_secret, secret_a)
        self.assertTrue(winner_session.rolled_back)

    async def test_restart_reads_same_persisted_secret(self):
        shared_db = {REDEMPTION_HMAC_SETTING_KEY: "p" * rss._REDEMPTION_SECRET_LENGTH}
        self._patch(lambda: FakeSecretSession(shared_db=shared_db))
        secret = await rss.ensure_redemption_secret()
        self.assertEqual(secret, "p" * rss._REDEMPTION_SECRET_LENGTH)
        # 重新创建实例后读取同一密钥
        reset_redemption_secret()
        self._patch(lambda: FakeSecretSession(shared_db=shared_db))
        secret_again = await rss.ensure_redemption_secret()
        self.assertEqual(secret_again, secret)


if __name__ == "__main__":
    unittest.main()
