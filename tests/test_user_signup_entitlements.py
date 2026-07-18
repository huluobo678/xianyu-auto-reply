from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend-web"))

from app.services.user_service import SIGNUP_AI_QUOTA, UserService  # noqa: E402
from common.models.ai_usage import AIQuotaConfig  # noqa: E402
from common.models.billing import (  # noqa: E402
    AIQuotaGrant,
    EntitlementLedger,
    UserSubscription,
)
from common.models.user import User  # noqa: E402
from common.schemas.user import UserCreate  # noqa: E402


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, free_plan):
        self.free_plan = free_plan
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.next_id = 10

    async def execute(self, _statement):
        return FakeResult(None)

    async def scalar(self, _statement):
        return self.free_plan

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = self.next_id
            self.next_id += 1
        self.added.append(value)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


class UserSignupEntitlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_atomically_creates_free_entitlements(self):
        free_plan = SimpleNamespace(
            id=1,
            code="free",
            account_limit=1,
            monthly_ai_quota=0,
            feature_flags=[
                "keyword_reply",
                "online_chat",
                "manual_takeover",
                "basic_auto_delivery",
            ],
        )
        session = FakeSession(free_plan)
        payload = UserCreate(
            username="new-user",
            email="new-user@example.com",
            password="Strong#Pass123",
        )
        now = datetime(2026, 7, 18, 10, 0, 0)

        with (
            patch("app.services.user_service.security.get_password_hash", return_value="hashed"),
            patch("app.services.user_service.get_beijing_now_naive", return_value=now),
        ):
            user = await UserService(session).create(payload)

        self.assertIsInstance(user, User)
        self.assertEqual(user.account_limit, 1)
        self.assertEqual(session.commits, 1)
        subscription = next(value for value in session.added if isinstance(value, UserSubscription))
        quota_config = next(value for value in session.added if isinstance(value, AIQuotaConfig))
        grant = next(value for value in session.added if isinstance(value, AIQuotaGrant))
        ledger = next(value for value in session.added if isinstance(value, EntitlementLedger))
        self.assertEqual(subscription.plan_code, "free")
        self.assertIn("basic_auto_delivery", subscription.feature_snapshot)
        self.assertEqual(quota_config.package_quota, 0)
        self.assertEqual(grant.grant_type, "signup_bonus")
        self.assertEqual(grant.total_quota, SIGNUP_AI_QUOTA)
        self.assertEqual(grant.remaining_quota, 100)
        self.assertEqual(ledger.quantity, 100)
        self.assertEqual(ledger.grant_id, grant.id)

    async def test_registration_stops_when_free_plan_is_missing(self):
        session = FakeSession(None)
        payload = UserCreate(
            username="new-user",
            email="new-user@example.com",
            password="Strong#Pass123",
        )

        with patch("app.services.user_service.security.get_password_hash", return_value="hashed"):
            with self.assertRaisesRegex(RuntimeError, "Free billing plan"):
                await UserService(session).create(payload)

        self.assertEqual(session.commits, 0)
        self.assertFalse(any(isinstance(value, AIQuotaGrant) for value in session.added))
