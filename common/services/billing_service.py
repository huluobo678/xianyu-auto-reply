"""Shared billing catalog and order creation service."""

from __future__ import annotations

import secrets
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.billing import (
    AIQuotaPackage,
    BillingOrder,
    BillingPlan,
    BillingPlanPrice,
)
from common.utils.time_utils import get_beijing_now_naive, safe_isoformat


class BillingCatalogError(ValueError):
    pass


class BillingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_catalog(self) -> dict:
        plans = list(
            await self.session.scalars(
                select(BillingPlan)
                .where(BillingPlan.enabled.is_(True))
                .order_by(BillingPlan.sort_order)
            )
        )
        prices = list(
            await self.session.scalars(
                select(BillingPlanPrice).where(BillingPlanPrice.enabled.is_(True))
            )
        )
        addons = list(
            await self.session.scalars(
                select(AIQuotaPackage)
                .where(AIQuotaPackage.enabled.is_(True))
                .order_by(AIQuotaPackage.sort_order)
            )
        )
        price_map: dict[int, list[dict]] = {}
        for price in prices:
            price_map.setdefault(price.plan_id, []).append(
                {
                    "id": price.id,
                    "billing_cycle": price.billing_cycle,
                    "duration_months": price.duration_months,
                    "amount": self._money(price.amount),
                    "currency": price.currency,
                }
            )
        return {
            "plans": [
                {
                    "id": plan.id,
                    "code": plan.code,
                    "name": plan.name,
                    "account_limit": plan.account_limit,
                    "monthly_ai_quota": int(plan.monthly_ai_quota),
                    "ai_unlimited": bool(plan.ai_unlimited),
                    "feature_flags": plan.feature_flags,
                    "is_free": bool(plan.is_free),
                    "prices": price_map.get(plan.id, []),
                }
                for plan in plans
            ],
            "ai_quota_packages": [
                {
                    "id": addon.id,
                    "code": addon.code,
                    "name": addon.name,
                    "base_quota": int(addon.base_quota),
                    "bonus_quota": int(addon.bonus_quota),
                    "total_quota": int(addon.base_quota + addon.bonus_quota),
                    "ai_unlimited": bool(addon.ai_unlimited),
                    "amount": self._money(addon.amount),
                    "validity_days": addon.validity_days,
                }
                for addon in addons
            ],
        }

    async def create_order(
        self, user_id: int, product_type: str, product_id: int, request_key: str
    ) -> tuple[BillingOrder, bool]:
        existing = await self.session.scalar(
            select(BillingOrder).where(
                BillingOrder.user_id == user_id,
                BillingOrder.request_key == request_key,
            )
        )
        if existing:
            return existing, True

        if product_type == "plan":
            product = await self.session.get(BillingPlanPrice, product_id)
            if not product or not product.enabled:
                raise BillingCatalogError("???????????")
            plan = await self.session.get(BillingPlan, product.plan_id)
            if not plan or not plan.enabled or plan.is_free:
                raise BillingCatalogError("??????????")
            code = f"{plan.code}:{product.billing_cycle}"
            name = f"{plan.name}-{product.billing_cycle}"
            snapshot = {
                "plan_id": plan.id,
                "plan_code": plan.code,
                "billing_cycle": product.billing_cycle,
                "duration_months": product.duration_months,
                "account_limit": plan.account_limit,
                "monthly_ai_quota": int(plan.monthly_ai_quota),
                "feature_flags": plan.feature_flags,
            }
        elif product_type == "ai_quota_package":
            product = await self.session.get(AIQuotaPackage, product_id)
            if not product or not product.enabled:
                raise BillingCatalogError("AI ??????????")
            code = product.code
            name = product.name
            snapshot = {
                "base_quota": int(product.base_quota),
                "bonus_quota": int(product.bonus_quota),
                "total_quota": int(product.base_quota + product.bonus_quota),
                "validity_days": product.validity_days,
            }
        else:
            raise BillingCatalogError("????????")

        order = BillingOrder(
            order_no=self._new_order_no(),
            user_id=user_id,
            request_key=request_key,
            product_type=product_type,
            product_id=product.id,
            product_code=code,
            product_name=name,
            product_snapshot=snapshot,
            amount=product.amount,
        )
        self.session.add(order)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(BillingOrder).where(
                    BillingOrder.user_id == user_id,
                    BillingOrder.request_key == request_key,
                )
            )
            if existing:
                return existing, True
            raise
        await self.session.refresh(order)
        return order, False

    @staticmethod
    def serialize_order(order: BillingOrder) -> dict:
        return {
            "order_no": order.order_no,
            "product_type": order.product_type,
            "product_code": order.product_code,
            "product_name": order.product_name,
            "amount": BillingService._money(order.amount),
            "currency": order.currency,
            "status": order.status,
            "payment_channel": order.payment_channel,
            "entitlement_status": order.entitlement_status,
            "created_at": safe_isoformat(order.created_at),
        }

    @staticmethod
    def _money(value: Decimal) -> str:
        return f"{Decimal(value):.2f}"

    @staticmethod
    def _new_order_no() -> str:
        stamp = get_beijing_now_naive().strftime("%Y%m%d%H%M%S%f")
        return f"B{stamp}{secrets.token_hex(3).upper()}"
