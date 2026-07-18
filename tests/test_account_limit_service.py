from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.services.account_limit_service import (  # noqa: E402
    AccountLimitExceededError,
    AccountLimitService,
)


class CountResult:
    def __init__(self, count):
        self.count = count

    def scalar(self):
        return self.count


class FakeSession:
    def __init__(self, user, count):
        self.user = user
        self.count = count

    async def get(self, _model, _user_id):
        return self.user

    async def execute(self, _statement):
        return CountResult(self.count)


class AccountLimitServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_effective_subscription_limit_overrides_stale_user_limit(self):
        session = FakeSession(SimpleNamespace(id=7, account_limit=15), 1)

        with patch(
            "common.services.account_limit_service.SubscriptionFeatureService.get_entitlements",
            new=AsyncMock(return_value={"account_limit": 1}),
        ):
            status = await AccountLimitService(session).get_status(7)

        self.assertEqual(status["account_limit"], 1)
        self.assertEqual(status["remaining_count"], 0)

    async def test_expired_subscription_blocks_new_account_immediately(self):
        session = FakeSession(SimpleNamespace(id=7, account_limit=15), 1)

        with patch(
            "common.services.account_limit_service.SubscriptionFeatureService.get_entitlements",
            new=AsyncMock(return_value={"account_limit": 1}),
        ):
            with self.assertRaises(AccountLimitExceededError):
                await AccountLimitService(session).ensure_can_add_account(7)


if __name__ == "__main__":
    unittest.main()
