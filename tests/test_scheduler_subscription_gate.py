from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]

from common.models.user import UserRole  # noqa: E402


def _load_scheduler_app():
    """加载 scheduler 子项目的 ``app`` 包。

    多个子项目（websocket / scheduler）顶层包都叫 ``app``，同一进程内先后导入会
    互相覆盖 ``sys.modules['app']``。本测试在用到前清除已缓存的 ``app`` 包并加载
    scheduler 的 ``app``，使其与 websocket 的 ``app`` 测试可在同一 unittest 进程内
    共存（各自在用到时加载所需 app）。
    """
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    sys.path.insert(0, str(ROOT / "scheduler"))
    from app.services.scheduler.listing_monitor_task import ListingMonitorTaskService
    from app.services.scheduler_service import SchedulerService

    return ListingMonitorTaskService, SchedulerService


class FakeSessionContext:
    def __init__(self, user):
        self.user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def get(self, _model, _user_id):
        return self.user



class AsyncContext:
    def __init__(self, value=None):
        self.value = value or self
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.value

    async def __aexit__(self, _exc_type, _exc, _traceback):
        self.exited = True
        return False


class SchedulerSession(AsyncContext):
    def __init__(self):
        super().__init__(self)
        self.transaction = AsyncContext()

    def begin(self):
        return self.transaction

class SchedulerSubscriptionGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ListingMonitorTaskService, self.SchedulerService = (
            _load_scheduler_app()
        )

    async def test_missing_owner_is_denied(self):
        allowed = await self.ListingMonitorTaskService()._owner_can_run_listing_monitor(None)

        self.assertFalse(allowed)

    async def test_admin_owner_bypasses_plan_check(self):
        admin = SimpleNamespace(id=1, role=UserRole.ADMIN)
        def session_factory():
            return FakeSessionContext(admin)

        with patch(
            'app.services.scheduler.listing_monitor_task.async_session_maker',
            session_factory,
        ):
            allowed = await self.ListingMonitorTaskService()._owner_can_run_listing_monitor(1)

        self.assertTrue(allowed)

    async def test_member_requires_listing_monitor_feature(self):
        member = SimpleNamespace(id=2, role=UserRole.MEMBER)
        def session_factory():
            return FakeSessionContext(member)

        with (
            patch(
                'app.services.scheduler.listing_monitor_task.async_session_maker',
                session_factory,
            ),
            patch(
                'app.services.scheduler.listing_monitor_task.SubscriptionFeatureService.has_any_feature',
                new=AsyncMock(return_value=False),
            ) as feature_check,
        ):
            allowed = await self.ListingMonitorTaskService()._owner_can_run_listing_monitor(2)

        self.assertFalse(allowed)
        feature_check.assert_awaited_once_with(2, ('listing_monitor',))
    async def test_subscription_expiry_scan_uses_transaction_and_batch_limit(self):
        session = SchedulerSession()
        lifecycle = AsyncMock(return_value=4)

        with patch(
            "app.services.scheduler_service.async_session_maker",
            return_value=session,
        ), patch(
            "app.services.scheduler_service.SubscriptionLifecycleService.expire_due_subscriptions",
            new=lifecycle,
        ):
            count = await self.SchedulerService()._expire_due_subscriptions_once()

        self.assertEqual(count, 4)
        lifecycle.assert_awaited_once_with(limit=500)
        self.assertTrue(session.entered)
        self.assertTrue(session.exited)
        self.assertTrue(session.transaction.entered)
        self.assertTrue(session.transaction.exited)
