from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.ai_usage import AIQuotaConfig
from common.models.billing import AIQuotaGrant, BillingOrder, EntitlementLedger, UserSubscription
from common.models.user import User
from common.utils.time_utils import get_beijing_now_naive


class BillingEntitlementError(RuntimeError):
    pass


class BillingEntitlementService:
    def __init__(self, session: AsyncSession):
        self.session = session
    @staticmethod
    def _normalize_feature_flags(value: object) -> list[str]:
        if isinstance(value, dict):
            return [str(key) for key, enabled in value.items() if enabled]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        raise BillingEntitlementError('Plan feature flags must be a list or object')

    async def grant_locked_order(
        self,
        order: BillingOrder,
        trade_no: str,
        now: datetime | None = None,
    ) -> BillingOrder:
        now = now or get_beijing_now_naive()
        if order.status == 'paid' and order.entitlement_status == 'granted':
            if order.channel_trade_no != trade_no:
                raise BillingEntitlementError('Paid order trade number mismatch')
            return order
        if order.status not in {'pending', 'paid'}:
            raise BillingEntitlementError(f'Order cannot be paid from status {order.status}')
        if order.channel_trade_no and order.channel_trade_no != trade_no:
            raise BillingEntitlementError('Order already belongs to another trade')

        order.entitlement_attempts = int(order.entitlement_attempts or 0) + 1
        order.entitlement_error = None
        order.payment_channel = 'alipay'
        order.channel_trade_no = trade_no
        order.status = 'paid'
        order.paid_at = order.paid_at or now

        if order.product_type == 'plan':
            await self._grant_plan(order, now)
        elif order.product_type == 'ai_quota_package':
            await self._grant_quota_package(order, now)
        else:
            raise BillingEntitlementError(f'Unsupported product type {order.product_type}')

        order.entitlement_status = 'granted'
        return order

    async def _grant_plan(self, order: BillingOrder, now: datetime) -> None:
        snapshot = order.product_snapshot or {}
        required = {
            'plan_id', 'plan_code', 'billing_cycle', 'duration_months',
            'account_limit', 'monthly_ai_quota', 'feature_flags',
        }
        missing = sorted(required.difference(snapshot))
        if missing:
            raise BillingEntitlementError(f'Plan snapshot missing: {", ".join(missing)}')

        user = await self.session.scalar(
            select(User).where(User.id == order.user_id).with_for_update()
        )
        if not user:
            raise BillingEntitlementError('Order user not found')

        subscription = await self.session.scalar(
            select(UserSubscription)
            .where(UserSubscription.user_id == order.user_id)
            .with_for_update()
        )
        duration_months = int(snapshot['duration_months'])
        same_active_plan = bool(
            subscription
            and subscription.status == 'active'
            and subscription.plan_code == snapshot['plan_code']
            and subscription.expires_at
            and subscription.expires_at > now
        )
        extension_base = subscription.expires_at if same_active_plan else now
        expires_at = self._add_months(extension_base, duration_months)
        period_start = now.date().replace(day=1)
        next_month = self._add_months(datetime(now.year, now.month, 1), 1)
        period_end = (next_month - timedelta(days=1)).date()

        if not subscription:
            subscription = UserSubscription(
                user_id=order.user_id,
                plan_id=int(snapshot['plan_id']),
                plan_code=str(snapshot['plan_code']),
                billing_cycle=str(snapshot['billing_cycle']),
                status='active',
                account_limit=int(snapshot['account_limit']),
                monthly_ai_quota=int(snapshot['monthly_ai_quota']),
                feature_snapshot=self._normalize_feature_flags(snapshot['feature_flags']),
                current_period_start=period_start,
                current_period_end=period_end,
                starts_at=now,
                expires_at=expires_at,
                source_order_id=order.id,
            )
            self.session.add(subscription)
        else:
            subscription.plan_id = int(snapshot['plan_id'])
            subscription.plan_code = str(snapshot['plan_code'])
            subscription.billing_cycle = str(snapshot['billing_cycle'])
            subscription.status = 'active'
            subscription.account_limit = int(snapshot['account_limit'])
            subscription.monthly_ai_quota = int(snapshot['monthly_ai_quota'])
            subscription.feature_snapshot = self._normalize_feature_flags(snapshot['feature_flags'])
            subscription.current_period_start = period_start
            subscription.current_period_end = period_end
            if not same_active_plan:
                subscription.starts_at = now
            subscription.expires_at = expires_at
            subscription.pending_plan_id = None
            subscription.source_order_id = order.id

        user.account_limit = int(snapshot['account_limit'])
        quota_config = await self._get_or_create_quota_config(order.user_id)
        quota_config.package_quota = int(snapshot['monthly_ai_quota'])

        await self._create_grant_and_ledger(
            order=order,
            grant_type='subscription',
            entitlement_type='monthly_ai_quota',
            quantity=int(snapshot['monthly_ai_quota']),
            starts_at=now,
            expires_at=expires_at,
            details={
                'plan_code': str(snapshot['plan_code']),
                'billing_cycle': str(snapshot['billing_cycle']),
                'duration_months': duration_months,
                'account_limit': int(snapshot['account_limit']),
            },
        )

    async def _grant_quota_package(self, order: BillingOrder, now: datetime) -> None:
        snapshot = order.product_snapshot or {}
        required = {'base_quota', 'bonus_quota', 'total_quota', 'validity_days'}
        missing = sorted(required.difference(snapshot))
        if missing:
            raise BillingEntitlementError(f'Quota package snapshot missing: {", ".join(missing)}')

        user = await self.session.scalar(
            select(User).where(User.id == order.user_id).with_for_update()
        )
        if not user:
            raise BillingEntitlementError('Order user not found')

        total_quota = int(snapshot['total_quota'])
        expires_at = now + timedelta(days=int(snapshot['validity_days']))
        await self._get_or_create_quota_config(order.user_id)

        await self._create_grant_and_ledger(
            order=order,
            grant_type='quota_package',
            entitlement_type='independent_ai_quota',
            quantity=total_quota,
            starts_at=now,
            expires_at=expires_at,
            details={
                'package_code': order.product_code,
                'base_quota': int(snapshot['base_quota']),
                'bonus_quota': int(snapshot['bonus_quota']),
                'validity_days': int(snapshot['validity_days']),
            },
        )

    async def _get_or_create_quota_config(self, user_id: int) -> AIQuotaConfig:
        config = await self.session.scalar(
            select(AIQuotaConfig)
            .where(AIQuotaConfig.user_id == user_id)
            .with_for_update()
        )
        if not config:
            config = AIQuotaConfig(user_id=user_id, package_quota=0, independent_quota=0)
            self.session.add(config)
        return config

    async def _create_grant_and_ledger(
        self,
        *,
        order: BillingOrder,
        grant_type: str,
        entitlement_type: str,
        quantity: int,
        starts_at: datetime,
        expires_at: datetime | None,
        details: dict,
    ) -> None:
        grant_key = f'billing-order:{order.id}:grant'
        grant = await self.session.scalar(
            select(AIQuotaGrant).where(AIQuotaGrant.idempotency_key == grant_key)
        )
        if not grant:
            grant = AIQuotaGrant(
                user_id=order.user_id,
                grant_type=grant_type,
                source_id=order.id,
                idempotency_key=grant_key,
                total_quota=quantity,
                remaining_quota=quantity,
                starts_at=starts_at,
                expires_at=expires_at,
                status='active',
            )
            self.session.add(grant)
            await self.session.flush()

        ledger_key = f'billing-order:{order.id}:ledger'
        ledger = await self.session.scalar(
            select(EntitlementLedger).where(EntitlementLedger.idempotency_key == ledger_key)
        )
        if not ledger:
            self.session.add(EntitlementLedger(
                user_id=order.user_id,
                event_type='payment_grant',
                entitlement_type=entitlement_type,
                quantity=quantity,
                order_id=order.id,
                grant_id=grant.id,
                idempotency_key=ledger_key,
                details=details,
            ))

    @staticmethod
    def _add_months(value: datetime, months: int) -> datetime:
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)
