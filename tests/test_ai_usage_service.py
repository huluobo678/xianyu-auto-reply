from __future__ import annotations

import sys
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "websocket"))

from common.services.ai_usage_service import (  # noqa: E402
    AIAccountConcurrencyError,
    AIQuotaExceededError,
    AIReservation,
    AIUsageService,
)


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, scalar_values):
        self.scalar_values = list(scalar_values)
        self.added = []
        self.executed = []

    def begin(self):
        return FakeTransaction()

    async def scalar(self, _statement):
        return self.scalar_values.pop(0)

    async def execute(self, statement, params=None):
        self.executed.append((statement, params))
        return SimpleNamespace()

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 99
        self.added.append(value)

    async def flush(self):
        return None


def usage(effective=0, reserved=0, cost=0):
    return SimpleNamespace(
        effective_replies=effective,
        reserved_replies=reserved,
        estimated_cost=cost,
        warned_80_at=None,
        warned_100_at=None,
    )


class AIUsageServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_quota_check_includes_reservations(self):
        with self.assertRaises(AIQuotaExceededError):
            AIUsageService._check_quota(usage(effective=9, reserved=1), 10, "user")

    async def test_account_concurrency_blocks_second_reservation(self):
        account = SimpleNamespace(id=8, owner_id=7, account_id="acct")
        account_config = SimpleNamespace(monthly_quota=None, max_concurrency=1, requests_per_minute=60)
        session = FakeSession([usage(), usage(reserved=1), None, account_config])
        with self.assertRaises(AIAccountConcurrencyError):
            await AIUsageService._reserve_locked(
                session, account, date(2026, 7, 1), "key", "msg", "chat", datetime(2026, 7, 16)
            )

    async def test_duplicate_request_returns_existing_reservation(self):
        existing = SimpleNamespace(id=42)
        session = FakeSession([existing])
        result = await AIUsageService.reserve(
            session,
            account_id="acct",
            idempotency_key="same-key",
            source_message_id="msg-1",
            chat_id="chat-1",
        )
        self.assertEqual(result, AIReservation(42, "same-key", True))

    async def test_commit_moves_reserved_to_effective_once(self):
        request = SimpleNamespace(
            status="reserved",
            user_id=7,
            account_pk=8,
            period_start=date(2026, 7, 1),
            estimated_cost="0.125",
            auto_reply_log_id=None,
            committed_at=None,
        )
        user_usage = usage(reserved=1)
        account_usage = usage(reserved=1)
        session = FakeSession([request, user_usage, account_usage, None])
        committed = await AIUsageService.commit(session, 1, 55)
        self.assertTrue(committed)
        self.assertEqual((user_usage.effective_replies, user_usage.reserved_replies), (1, 0))
        self.assertEqual((account_usage.effective_replies, account_usage.reserved_replies), (1, 0))
        self.assertEqual(request.status, "committed")
        self.assertEqual(request.auto_reply_log_id, 55)

    async def test_release_restores_reserved_counters(self):
        request = SimpleNamespace(
            status="reserved",
            user_id=7,
            account_pk=8,
            period_start=date(2026, 7, 1),
            release_reason=None,
            released_at=None,
        )
        user_usage = usage(reserved=1)
        account_usage = usage(reserved=1)
        session = FakeSession([request, user_usage, account_usage])
        released = await AIUsageService.release(session, 1, "send_failed")
        self.assertTrue(released)
        self.assertEqual((user_usage.reserved_replies, account_usage.reserved_replies), (0, 0))
        self.assertEqual(request.status, "released")

    async def test_threshold_marks_eighty_percent_once(self):
        quota = SimpleNamespace(package_quota=6, independent_quota=4)
        monthly_usage = usage(effective=8)
        monthly_usage.period_start = date(2026, 7, 1)
        session = FakeSession([quota])
        now = datetime(2026, 7, 16)
        await AIUsageService._mark_thresholds(session, 7, monthly_usage, now)
        self.assertEqual(monthly_usage.warned_80_at, now)
        self.assertIsNone(monthly_usage.warned_100_at)

    async def test_send_failure_releases_reservation(self):
        from app.services.xianyu.auto_reply_service import AutoReplyService

        service = AutoReplyService.__new__(AutoReplyService)
        service.cookie_id = "acct"
        service.xianyu_instance = SimpleNamespace(
            wait_send_reject_reason=AsyncMock(return_value="CSI_FORBID")
        )
        service.auto_reply_log_service = SimpleNamespace(
            safe_update_send_status=AsyncMock()
        )

        @asynccontextmanager
        async def fake_session_maker():
            yield SimpleNamespace()

        with patch(
            "app.services.xianyu.auto_reply_service.async_session_maker",
            fake_session_maker,
        ), patch.object(AIUsageService, "release", new=AsyncMock(return_value=True)) as release:
            await service._writeback_send_status(55, [(SimpleNamespace(), "mid")], 77)

        release.assert_awaited_once()
        self.assertEqual(release.await_args.args[1:], (77, "send_failed"))
        service.auto_reply_log_service.safe_update_send_status.assert_awaited_once()

    async def test_confirmed_send_commits_reservation(self):
        from app.services.xianyu.auto_reply_service import AutoReplyService

        service = AutoReplyService.__new__(AutoReplyService)
        service.cookie_id = "acct"
        service.xianyu_instance = SimpleNamespace(
            wait_send_outcome=AsyncMock(return_value=("confirmed", None))
        )
        service.auto_reply_log_service = SimpleNamespace(safe_update_send_status=AsyncMock())

        @asynccontextmanager
        async def fake_session_maker():
            yield SimpleNamespace()

        with patch(
            "app.services.xianyu.auto_reply_service.async_session_maker",
            fake_session_maker,
        ), patch.object(AIUsageService, "commit", new=AsyncMock(return_value=True)) as commit:
            await service._writeback_send_status(55, [(SimpleNamespace(), "mid")], 77)

        commit.assert_awaited_once()
        self.assertEqual(commit.await_args.args[1:], (77, 55))

    async def test_unconfirmed_send_releases_reservation(self):
        from app.services.xianyu.auto_reply_service import AutoReplyService

        service = AutoReplyService.__new__(AutoReplyService)
        service.cookie_id = "acct"
        service.xianyu_instance = SimpleNamespace(
            wait_send_outcome=AsyncMock(return_value=("unknown", None))
        )
        service.auto_reply_log_service = SimpleNamespace(safe_update_send_status=AsyncMock())

        @asynccontextmanager
        async def fake_session_maker():
            yield SimpleNamespace()

        with patch(
            "app.services.xianyu.auto_reply_service.async_session_maker",
            fake_session_maker,
        ), patch.object(AIUsageService, "release", new=AsyncMock(return_value=True)) as release:
            await service._writeback_send_status(55, [(SimpleNamespace(), "mid")], 77)

        release.assert_awaited_once()
        self.assertEqual(release.await_args.args[1:], (77, "send_unconfirmed"))

    async def test_writeback_exception_releases_reservation(self):
        from app.services.xianyu.auto_reply_service import AutoReplyService

        service = AutoReplyService.__new__(AutoReplyService)
        service.cookie_id = "acct"
        service.xianyu_instance = SimpleNamespace(
            wait_send_outcome=AsyncMock(side_effect=RuntimeError("ack failure"))
        )
        service.auto_reply_log_service = SimpleNamespace(safe_update_send_status=AsyncMock())

        @asynccontextmanager
        async def fake_session_maker():
            yield SimpleNamespace()

        with patch(
            "app.services.xianyu.auto_reply_service.async_session_maker",
            fake_session_maker,
        ), patch.object(AIUsageService, "release", new=AsyncMock(return_value=True)) as release:
            await service._writeback_send_status(55, [(SimpleNamespace(), "mid")], 77)

        release.assert_awaited_once()
        self.assertEqual(release.await_args.args[1:], (77, "writeback_exception"))


if __name__ == "__main__":
    unittest.main()
