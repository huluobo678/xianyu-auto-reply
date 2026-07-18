from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend-web'))

from app.services.billing_payment_service import (  # noqa: E402
    BillingPaymentService,
    BillingPaymentValidationError,
)


class FakeSession:
    def __init__(self, order=None):
        self.order = order
        self.commits = 0
        self.rollbacks = 0

    async def scalar(self, _statement):
        return self.order

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class BillingPaymentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.order = SimpleNamespace(
            order_no='B100',
            amount=Decimal('39.00'),
            channel_trade_no=None,
            status='pending',
            notify_received_at=None,
            payment_qr_code='qr',
            payment_expires_at=None,
        )
        self.config = {
            'app_id': 'app',
            'private_key': 'private',
            'alipay_public_key': 'public',
            'seller_id': '2088000000000000',
            'billing_notify_url': 'https://example.com/api/v1/billing/alipay/notify',
        }

    def test_callback_validation_accepts_exact_amount_and_seller(self):
        status, trade_no = BillingPaymentService._validate_callback(
            self.order,
            {
                'seller_id': self.config['seller_id'],
                'total_amount': '39.00',
                'trade_status': 'TRADE_SUCCESS',
                'trade_no': 'ALI-100',
            },
            self.config,
        )
        self.assertEqual(status, 'TRADE_SUCCESS')
        self.assertEqual(trade_no, 'ALI-100')

    def test_callback_rejects_amount_tampering(self):
        with self.assertRaises(BillingPaymentValidationError):
            BillingPaymentService._validate_callback(
                self.order,
                {
                    'seller_id': self.config['seller_id'],
                    'total_amount': '0.01',
                    'trade_status': 'TRADE_SUCCESS',
                    'trade_no': 'ALI-100',
                },
                self.config,
            )

    def test_callback_rejects_wrong_seller(self):
        with self.assertRaises(BillingPaymentValidationError):
            BillingPaymentService._validate_callback(
                self.order,
                {
                    'seller_id': 'another-seller',
                    'total_amount': '39.00',
                    'trade_status': 'TRADE_SUCCESS',
                    'trade_no': 'ALI-100',
                },
                self.config,
            )

    async def test_valid_notify_commits_entitlement_once(self):
        session = FakeSession(self.order)
        grant = AsyncMock()
        with (
            patch(
                'app.services.billing_payment_service.AlipayService.load_config',
                new=AsyncMock(return_value=self.config),
            ),
            patch(
                'app.services.billing_payment_service.AlipayService.verify_notify',
                return_value=True,
            ),
            patch(
                'app.services.billing_payment_service.BillingEntitlementService.grant_locked_order',
                new=grant,
            ),
        ):
            ok = await BillingPaymentService(session).handle_alipay_notify({
                'app_id': 'app',
                'sign': 'signed',
                'seller_id': self.config['seller_id'],
                'out_trade_no': 'B100',
                'total_amount': '39.00',
                'trade_status': 'TRADE_SUCCESS',
                'trade_no': 'ALI-100',
            })

        self.assertTrue(ok)
        grant.assert_awaited_once()
        self.assertEqual(session.commits, 1)
        self.assertEqual(session.rollbacks, 0)
        self.assertIsNone(self.order.payment_qr_code)

    async def test_invalid_amount_rolls_back_without_grant(self):
        session = FakeSession(self.order)
        grant = AsyncMock()
        with (
            patch(
                'app.services.billing_payment_service.AlipayService.load_config',
                new=AsyncMock(return_value=self.config),
            ),
            patch(
                'app.services.billing_payment_service.AlipayService.verify_notify',
                return_value=True,
            ),
            patch(
                'app.services.billing_payment_service.BillingEntitlementService.grant_locked_order',
                new=grant,
            ),
        ):
            ok = await BillingPaymentService(session).handle_alipay_notify({
                'app_id': 'app',
                'sign': 'signed',
                'seller_id': self.config['seller_id'],
                'out_trade_no': 'B100',
                'total_amount': '0.01',
                'trade_status': 'TRADE_SUCCESS',
                'trade_no': 'ALI-100',
            })

        self.assertFalse(ok)
        grant.assert_not_awaited()
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.rollbacks, 1)
