from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.alipay_service import AlipayService
from common.models.billing import BillingOrder
from common.services.billing_entitlement_service import (
    BillingEntitlementError,
    BillingEntitlementService,
)
from common.utils.time_utils import get_beijing_now_naive, safe_isoformat

logger = logging.getLogger(__name__)


class BillingPaymentError(RuntimeError):
    pass


class BillingPaymentNotConfigured(BillingPaymentError):
    pass


class BillingPaymentValidationError(BillingPaymentError):
    pass


class BillingPaymentService:
    REQUIRED_CONFIG_KEYS = (
        'app_id', 'private_key', 'alipay_public_key',
        'seller_id', 'billing_notify_url',
    )

    def __init__(self, session: AsyncSession):
        self.session = session

    async def payment_ready(self) -> bool:
        config = await AlipayService.load_config(self.session)
        return self._config_ready(config)

    async def create_alipay_payment(self, user_id: int, order_no: str) -> dict[str, Any]:
        order = await self.session.scalar(select(BillingOrder).where(
            BillingOrder.order_no == order_no,
            BillingOrder.user_id == user_id,
        ))
        if not order:
            raise BillingPaymentError('Billing order not found')
        if order.status == 'paid':
            return self.serialize_payment(order, payment_ready=True)
        if order.status != 'pending':
            raise BillingPaymentError(f'Billing order is {order.status}')

        config = await AlipayService.load_config(self.session)
        if not self._config_ready(config):
            raise BillingPaymentNotConfigured('Alipay billing channel is not configured')

        now = get_beijing_now_naive()
        if order.payment_qr_code and order.payment_expires_at and order.payment_expires_at > now:
            return self.serialize_payment(order, payment_ready=True)

        alipay = AlipayService(config)
        result = await asyncio.to_thread(
            alipay.create_f2f_pay,
            {
                'out_trade_no': order.order_no,
                'total_amount': f'{Decimal(order.amount):.2f}',
                'subject': order.product_name[:128],
                'body': f'{order.product_type}:{order.product_code}',
                'timeout_express': '30m',
                'notify_url': config['billing_notify_url'],
            },
        )
        if not result or not result.get('success') or not result.get('qr_code'):
            raise BillingPaymentError('Failed to create Alipay payment QR code')

        order.payment_channel = 'alipay'
        order.payment_qr_code = str(result['qr_code'])
        order.payment_expires_at = now + timedelta(minutes=30)
        await self.session.commit()
        await self.session.refresh(order)
        return self.serialize_payment(order, payment_ready=True)

    async def get_order(self, user_id: int, order_no: str) -> BillingOrder | None:
        return await self.session.scalar(select(BillingOrder).where(
            BillingOrder.order_no == order_no,
            BillingOrder.user_id == user_id,
        ))

    async def handle_alipay_notify(self, notify_data: dict[str, Any]) -> bool:
        config = await AlipayService.load_config(self.session)
        if not self._config_ready(config):
            logger.error('Billing callback rejected: Alipay billing is not configured')
            return False

        try:
            alipay = AlipayService(config)
        except ValueError:
            logger.error('Billing callback rejected: invalid Alipay configuration')
            return False
        if not alipay.verify_notify(notify_data):
            return False

        order_no = str(notify_data.get('out_trade_no') or '')
        if not order_no:
            logger.warning('Billing callback rejected: missing order number')
            return False

        try:
            order = await self.session.scalar(
                select(BillingOrder)
                .where(BillingOrder.order_no == order_no)
                .with_for_update()
            )
            if not order:
                logger.warning('Billing callback rejected: order not found')
                await self.session.rollback()
                return False

            trade_status, trade_no = self._validate_callback(order, notify_data, config)
            now = get_beijing_now_naive()
            order.notify_received_at = now

            if trade_status == 'TRADE_CLOSED':
                if order.status == 'pending':
                    order.status = 'closed'
                    order.closed_at = now
                    order.payment_qr_code = None
                    order.payment_expires_at = None
                await self.session.commit()
                return True
            if not AlipayService.is_trade_success(trade_status):
                await self.session.rollback()
                return True

            await BillingEntitlementService(self.session).grant_locked_order(
                order, trade_no, now
            )
            order.payment_qr_code = None
            order.payment_expires_at = None
            await self.session.commit()
            logger.info('Billing payment settled: order=%s', order.order_no)
            return True
        except (BillingPaymentValidationError, BillingEntitlementError) as exc:
            await self.session.rollback()
            logger.warning('Billing callback rejected for order=%s: %s', order_no, exc)
            return False
        except Exception:
            await self.session.rollback()
            logger.exception('Billing callback processing failed for order=%s', order_no)
            return False

    @classmethod
    def _config_ready(cls, config: dict[str, str]) -> bool:
        return all(str(config.get(key) or '').strip() for key in cls.REQUIRED_CONFIG_KEYS)

    @staticmethod
    def _validate_callback(
        order: BillingOrder,
        notify_data: dict[str, Any],
        config: dict[str, str],
    ) -> tuple[str, str]:
        seller_id = str(notify_data.get('seller_id') or '')
        if not seller_id or seller_id != str(config.get('seller_id') or ''):
            raise BillingPaymentValidationError('Alipay seller_id mismatch')

        try:
            paid_amount = Decimal(str(notify_data.get('total_amount'))).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BillingPaymentValidationError('Invalid callback amount') from exc
        expected_amount = Decimal(order.amount).quantize(Decimal('0.01'))
        if paid_amount != expected_amount:
            raise BillingPaymentValidationError('Alipay callback amount mismatch')

        trade_status = str(notify_data.get('trade_status') or '')
        trade_no = str(notify_data.get('trade_no') or '')
        if AlipayService.is_trade_success(trade_status) and not trade_no:
            raise BillingPaymentValidationError('Missing Alipay trade number')
        if order.channel_trade_no and trade_no and order.channel_trade_no != trade_no:
            raise BillingPaymentValidationError('Alipay trade number mismatch')
        return trade_status, trade_no

    @staticmethod
    def serialize_payment(order: BillingOrder, *, payment_ready: bool) -> dict[str, Any]:
        return {
            'order_no': order.order_no,
            'status': order.status,
            'payment_channel': order.payment_channel,
            'payment_ready': payment_ready,
            'qr_code': order.payment_qr_code if order.status == 'pending' else None,
            'payment_expires_at': safe_isoformat(order.payment_expires_at),
            'entitlement_status': order.entitlement_status,
            'paid_at': safe_isoformat(order.paid_at),
        }
