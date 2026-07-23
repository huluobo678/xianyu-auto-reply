from __future__ import annotations

import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_scheduler_app():
    """加载 scheduler 子项目的 ``app`` 包。

    多个子项目（websocket / scheduler / backend-web）顶层包都叫 ``app``，同一进程内
    先后导入会互相覆盖 ``sys.modules['app']``。本测试在用到前清除已缓存的 ``app``
    包并加载 scheduler 的 ``app``，遵循 ``test_scheduler_subscription_gate`` 的既有约定。
    """
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    sys.path.insert(0, str(ROOT / "scheduler"))
    from app.services import scheduler_service as svc  # noqa: E402

    return svc


from common.services import entitlement_grant_service as egs  # noqa: E402


class FakeSavepointSession:
    """模拟带 savepoint 的会话：单条失败只回滚 savepoint，不毒化外层事务。"""

    def __init__(self, subscriptions):
        self._subscriptions = subscriptions
        self.begin_called = 0
        self.begin_nested_called = 0
        self.savepoint_rollbacks = 0
        self.outer_rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        self.begin_called += 1

        @asynccontextmanager
        async def _outer():
            try:
                yield
            except Exception:
                self.outer_rolled_back = True
                raise

        return _outer()

    def begin_nested(self):
        self.begin_nested_called += 1
        outer = self

        @asynccontextmanager
        async def _savepoint():
            try:
                yield
            except Exception:
                # savepoint 回滚，外层事务保持可用
                outer.savepoint_rollbacks += 1
                raise

        return _savepoint()

    async def scalars(self, _stmt):
        return list(self._subscriptions)

    async def scalar(self, _stmt):
        return None

    def add(self, _value):
        return None

    async def flush(self):
        return None


class QuarterlySavepointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._svc = _load_scheduler_app()
        self._orig_maker = self._svc.async_session_maker
        self._orig_grant = egs.EntitlementGrantService.grant_scheduled_monthly_quota

    def tearDown(self):
        self._svc.async_session_maker = self._orig_maker
        egs.EntitlementGrantService.grant_scheduled_monthly_quota = self._orig_grant
        # 清除本测试加载的 scheduler ``app`` 包缓存与注入的 scheduler 路径，避免与后续
        # 测试（如 test_ai_usage_service 依赖 websocket/backend-web 的 ``app``）冲突。
        scheduler_path = str(ROOT / "scheduler")
        while scheduler_path in sys.path:
            sys.path.remove(scheduler_path)
        for key in list(sys.modules):
            if key == "app" or key.startswith("app."):
                del sys.modules[key]

    async def test_single_failure_isolated_others_still_processed(self):
        sub_a = SimpleNamespace(
            id=1,
            user_id=7,
            ai_unlimited=False,
            monthly_ai_quota=1000,
            billing_cycle="quarterly",
            plan_code="standard",
            status="active",
            expires_at=None,
        )
        sub_b = SimpleNamespace(
            id=2,
            user_id=8,
            ai_unlimited=False,
            monthly_ai_quota=1000,
            billing_cycle="quarterly",
            plan_code="standard",
            status="active",
            expires_at=None,
        )
        session = FakeSavepointSession([sub_a, sub_b])
        self._svc.async_session_maker = lambda: session

        async def fake_grant(self, subscription, now):  # noqa: ANN001
            if subscription.id == 1:
                raise RuntimeError("simulated db error for subscription 1")
            return 99

        egs.EntitlementGrantService.grant_scheduled_monthly_quota = fake_grant

        scheduler = self._svc.SchedulerService()
        processed = await scheduler._grant_quarterly_monthly_quota_once()

        # 单条失败被 savepoint 隔离：另一条仍被处理
        self.assertEqual(processed, 1)
        # 每个订阅各进入一次 savepoint
        self.assertEqual(session.begin_nested_called, 2)
        # 失败的那次 savepoint 被回滚
        self.assertEqual(session.savepoint_rollbacks, 1)
        # 外层事务未被毒化/回滚
        self.assertFalse(session.outer_rolled_back)


if __name__ == "__main__":
    unittest.main()
