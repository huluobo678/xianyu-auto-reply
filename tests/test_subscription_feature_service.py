from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend-web'))

from app.api.deps import require_billing_features  # noqa: E402
from common.models.user import UserRole  # noqa: E402
from common.services.subscription_feature_service import SubscriptionFeatureService  # noqa: E402


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None


class SubscriptionFeatureServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_subscription_exposes_expanded_features(self):
        subscription = SimpleNamespace(
            plan_code='merchant',
            account_limit=5,
            monthly_ai_quota=5000,
            feature_snapshot=['batch_publish', 'listing_monitor'],
            expires_at=datetime(2026, 8, 18, 12, 0, 0),
        )
        entitlements = await SubscriptionFeatureService(FakeSession([subscription])).get_entitlements(7)

        self.assertEqual(entitlements['plan_code'], 'merchant')
        self.assertIn('single_publish', entitlements['features'])
        self.assertIn('batch_publish', entitlements['features'])
        self.assertEqual(entitlements['source'], 'subscription')

    async def test_missing_subscription_falls_back_to_free_plan(self):
        free_plan = SimpleNamespace(
            account_limit=1,
            monthly_ai_quota=0,
            feature_flags=['keyword_reply', 'basic_auto_delivery'],
        )
        entitlements = await SubscriptionFeatureService(FakeSession([None, free_plan])).get_entitlements(8)

        self.assertEqual(entitlements['plan_code'], 'free')
        self.assertEqual(entitlements['account_limit'], 1)
        self.assertIn('basic_auto_delivery', entitlements['features'])
        self.assertNotIn('single_publish', entitlements['features'])

    def test_legacy_feature_object_is_supported(self):
        features = SubscriptionFeatureService.expand_feature_flags({
            'batch_publish': True,
            'api_access': False,
        })

        self.assertEqual(features, {'batch_publish', 'single_publish'})


class BillingFeatureDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_bypasses_subscription_check(self):
        dependency = require_billing_features('listing_monitor')
        admin = SimpleNamespace(id=1, role=UserRole.ADMIN)

        result = await dependency(current_user=admin, session=FakeSession())

        self.assertIs(result, admin)

    async def test_member_is_denied_when_plan_lacks_feature(self):
        dependency = require_billing_features('batch_publish')
        member = SimpleNamespace(id=2, role=UserRole.MEMBER)

        with patch(
            'app.api.deps.SubscriptionFeatureService.has_any_feature',
            return_value=False,
        ):
            with self.assertRaises(HTTPException) as raised:
                await dependency(current_user=member, session=FakeSession())

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn('套餐与额度', str(raised.exception.detail))

    async def test_member_is_allowed_when_plan_has_feature(self):
        dependency = require_billing_features('single_publish', 'batch_publish')
        member = SimpleNamespace(id=3, role=UserRole.MEMBER)

        with patch(
            'app.api.deps.SubscriptionFeatureService.has_any_feature',
            return_value=True,
        ):
            result = await dependency(current_user=member, session=FakeSession())

        self.assertIs(result, member)
