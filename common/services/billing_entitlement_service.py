from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from common.models.billing import BillingOrder
from common.services.entitlement_grant_service import (
    EntitlementGrantService,
)
from common.utils.time_utils import get_beijing_now_naive


class BillingEntitlementError(RuntimeError):
    pass


class BillingEntitlementService:
    """支付宝订单权益发放的薄包装。

    通用发放核心已抽取至 ``EntitlementGrantService``；本类只负责支付宝回调特有的
    订单状态/交易号校验，然后把订单快照转换为通用发放参数后委托核心服务发放。

    - 历史订单仍能读取和发放（同一套发放核心）。
    - 兑换码不走本入口（不伪装成支付宝订单），由 ``RedemptionService`` 直接调用
      ``EntitlementGrantService``。
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _normalize_feature_flags(value: object) -> list[str]:
        # 委托通用发放服务，保持与 EntitlementGrantService 一致；供 Lifecycle 复用。
        return EntitlementGrantService._normalize_feature_flags(value)

    async def grant_locked_order(
        self,
        order: BillingOrder,
        trade_no: str,
        now: datetime | None = None,
    ) -> BillingOrder:
        now = now or get_beijing_now_naive()
        if order.status == "paid" and order.entitlement_status == "granted":
            if order.channel_trade_no != trade_no:
                raise BillingEntitlementError("Paid order trade number mismatch")
            return order
        if order.status not in {"pending", "paid"}:
            raise BillingEntitlementError(
                f"Order cannot be paid from status {order.status}"
            )
        if order.channel_trade_no and order.channel_trade_no != trade_no:
            raise BillingEntitlementError("Order already belongs to another trade")

        order.entitlement_attempts = int(order.entitlement_attempts or 0) + 1
        order.entitlement_error = None
        order.payment_channel = "alipay"
        order.channel_trade_no = trade_no
        order.status = "paid"
        order.paid_at = order.paid_at or now

        snapshot = order.product_snapshot or {}
        idempotency_key = f"billing-order:{order.id}"
        source = {
            "kind": "alipay",
            "event_type": "payment_grant",
            "order_id": order.id,
        }
        grant_service = EntitlementGrantService(self.session)

        if order.product_type == "plan":
            required = {
                "plan_id",
                "plan_code",
                "billing_cycle",
                "duration_months",
                "account_limit",
                "monthly_ai_quota",
                "feature_flags",
            }
            missing = sorted(required.difference(snapshot))
            if missing:
                raise BillingEntitlementError(
                    f"Plan snapshot missing: {', '.join(missing)}"
                )
            plan_snapshot = dict(snapshot)
            plan_snapshot.setdefault("ai_unlimited", False)
            await grant_service.grant_plan(
                user_id=order.user_id,
                plan_snapshot=plan_snapshot,
                billing_cycle=str(snapshot["billing_cycle"]),
                duration_months=int(snapshot["duration_months"]),
                idempotency_key=idempotency_key,
                source=source,
                now=now,
            )
        elif order.product_type == "ai_quota_package":
            required = {"base_quota", "bonus_quota", "total_quota", "validity_days"}
            missing = sorted(required.difference(snapshot))
            if missing:
                raise BillingEntitlementError(
                    f"Quota package snapshot missing: {', '.join(missing)}"
                )
            package_snapshot = dict(snapshot)
            package_snapshot.setdefault("package_code", order.product_code)
            package_snapshot.setdefault("ai_unlimited", False)
            await grant_service.grant_quota_package(
                user_id=order.user_id,
                package_snapshot=package_snapshot,
                idempotency_key=idempotency_key,
                source=source,
                now=now,
            )
        else:
            raise BillingEntitlementError(
                f"Unsupported product type {order.product_type}"
            )

        order.entitlement_status = "granted"
        return order

    # 保留旧静态工具委托，避免外部直接引用被破坏
    @staticmethod
    def _add_months(value: datetime, months: int) -> datetime:
        return EntitlementGrantService._add_months(value, months)
