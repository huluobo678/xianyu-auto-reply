from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from common.models.ai_usage import AIQuotaConfig
from common.models.billing import AIQuotaGrant, BillingOrder, EntitlementLedger, UserSubscription
from common.services.billing_entitlement_service import (
    BillingEntitlementError,
    BillingEntitlementService,
)


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])
        self.added = []
        self.flushes = 0
        self.next_id = 100

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, value):
        if getattr(value, 'id', None) is None:
            value.id = self.next_id
            self.next_id += 1
        self.added.append(value)

    async def flush(self):
        self.flushes += 1


class BillingEntitlementServiceTests(unittest.IsolatedAsyncioTestCase):
    def _plan_order(self):
        return BillingOrder(
            id=10,
            order_no='B100',
            user_id=7,
            request_key='request-100',
            product_type='plan',
            product_id=2,
            product_code='standard:monthly',
            product_name='标准版-月付',
            product_snapshot={
                'plan_id': 2,
                'plan_code': 'standard',
                'billing_cycle': 'monthly',
                'duration_months': 1,
                'account_limit': 2,
                'monthly_ai_quota': 1000,
                'feature_flags': {'auto_delivery': True},
            },
            amount=Decimal('39.00'),
            status='pending',
            entitlement_status='pending',
            entitlement_attempts=0,
        )

    async def test_plan_payment_atomically_grants_subscription_and_limits(self):
        user = SimpleNamespace(id=7, account_limit=1)
        session = FakeSession([user, None, None, None, None])
        order = self._plan_order()
        now = datetime(2026, 7, 18, 12, 0, 0)

        result = await BillingEntitlementService(session).grant_locked_order(
            order, 'ALI-100', now
        )

        self.assertIs(result, order)
        self.assertEqual(order.status, 'paid')
        self.assertEqual(order.entitlement_status, 'granted')
        self.assertEqual(order.channel_trade_no, 'ALI-100')
        self.assertEqual(user.account_limit, 2)
        subscription = next(value for value in session.added if isinstance(value, UserSubscription))
        quota = next(value for value in session.added if isinstance(value, AIQuotaConfig))
        grant = next(value for value in session.added if isinstance(value, AIQuotaGrant))
        ledger = next(value for value in session.added if isinstance(value, EntitlementLedger))
        self.assertEqual(subscription.plan_code, 'standard')
        self.assertEqual(subscription.expires_at, datetime(2026, 8, 18, 12, 0, 0))
        self.assertEqual(quota.package_quota, 1000)
        self.assertEqual(grant.total_quota, 1000)
        self.assertEqual(ledger.order_id, order.id)

    async def test_quota_package_creates_grant_without_inflating_manual_quota(self):
        user = SimpleNamespace(id=7, account_limit=2)
        quota = AIQuotaConfig(user_id=7, package_quota=1000, independent_quota=300)
        session = FakeSession([user, quota, None, None])
        order = BillingOrder(
            id=11,
            order_no='B101',
            user_id=7,
            request_key='request-101',
            product_type='ai_quota_package',
            product_id=3,
            product_code='quota-1000',
            product_name='AI加量包',
            product_snapshot={
                'base_quota': 1000,
                'bonus_quota': 300,
                'total_quota': 1300,
                'validity_days': 180,
            },
            amount=Decimal('29.00'),
            status='pending',
            entitlement_status='pending',
            entitlement_attempts=0,
        )

        await BillingEntitlementService(session).grant_locked_order(
            order, 'ALI-101', datetime(2026, 7, 18, 12, 0, 0)
        )

        self.assertEqual(quota.independent_quota, 300)
        self.assertEqual(order.entitlement_status, 'granted')
        grants = [value for value in session.added if isinstance(value, AIQuotaGrant)]
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].remaining_quota, 1300)

    async def test_duplicate_callback_does_not_grant_twice(self):
        session = FakeSession()
        order = self._plan_order()
        order.status = 'paid'
        order.entitlement_status = 'granted'
        order.channel_trade_no = 'ALI-100'

        await BillingEntitlementService(session).grant_locked_order(
            order, 'ALI-100', datetime(2026, 7, 18, 12, 0, 0)
        )

        self.assertEqual(session.added, [])
        self.assertEqual(order.entitlement_attempts, 0)

    async def test_paid_order_rejects_different_trade_number(self):
        session = FakeSession()
        order = self._plan_order()
        order.status = 'paid'
        order.entitlement_status = 'granted'
        order.channel_trade_no = 'ALI-100'

        with self.assertRaises(BillingEntitlementError):
            await BillingEntitlementService(session).grant_locked_order(
                order, 'ALI-OTHER'
            )
