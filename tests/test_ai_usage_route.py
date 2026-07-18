from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend-web"))

from app.api.routes.ai_usage import get_my_ai_usage  # noqa: E402


class FakeSession:
    def __init__(self, scalar_values):
        self.scalar_values = list(scalar_values)

    async def scalar(self, _statement):
        return self.scalar_values.pop(0)


class AIUsageRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_customer_usage_ignores_expired_package_quota(self):
        usage = SimpleNamespace(effective_replies=10)
        stale_config = SimpleNamespace(package_quota=5000, independent_quota=20)
        session = FakeSession([usage, stale_config, 30, 100])

        with patch(
            "app.api.routes.ai_usage.SubscriptionFeatureService.get_entitlements",
            new=AsyncMock(return_value={"monthly_ai_quota": 0}),
        ):
            response = await get_my_ai_usage(
                current_user=SimpleNamespace(id=7),
                session=session,
            )

        self.assertEqual(
            response.data,
            {"effective_replies": 10, "remaining_quota": 40},
        )


if __name__ == "__main__":
    unittest.main()
