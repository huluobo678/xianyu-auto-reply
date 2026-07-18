from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scheduler'))

from app.services.scheduler.listing_monitor_task import ListingMonitorTaskService  # noqa: E402
from common.models.user import UserRole  # noqa: E402


class FakeSessionContext:
    def __init__(self, user):
        self.user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def get(self, _model, _user_id):
        return self.user


class SchedulerSubscriptionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_owner_is_denied(self):
        allowed = await ListingMonitorTaskService()._owner_can_run_listing_monitor(None)

        self.assertFalse(allowed)

    async def test_admin_owner_bypasses_plan_check(self):
        admin = SimpleNamespace(id=1, role=UserRole.ADMIN)
        def session_factory():
            return FakeSessionContext(admin)

        with patch(
            'app.services.scheduler.listing_monitor_task.async_session_maker',
            session_factory,
        ):
            allowed = await ListingMonitorTaskService()._owner_can_run_listing_monitor(1)

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
            allowed = await ListingMonitorTaskService()._owner_can_run_listing_monitor(2)

        self.assertFalse(allowed)
        feature_check.assert_awaited_once_with(2, ('listing_monitor',))
