"""支付宝入口守卫测试（阶段三）。

验证 ``alipay.enabled`` 默认关闭（设置项缺失即视为 false）时：

- 受守卫入口返回 503 支付未开通，不调用支付宝 SDK、不创建真实支付订单、
  不修改余额/订阅权益：
  - 套餐支付 ``/billing/orders/{no}/pay``、订单创建/查询、``payment-readiness``；
  - 余额充值 ``/payment/recharge``、``/payment/recharge/{no}``；
  - 广告付款 ``/advertisements/{id}/pay`` 及其轮询回调；
  - 支付宝回调 ``/billing/alipay/notify``、``/payment/alipay/notify``。
- 下列功能仍正常可用（不被总开关误伤）：
  - ``/billing/catalog``、``/billing/entitlements``；
  - ``/payment/withdraw``、``/payment/settlement-records``；
  - 兑换码兑换 ``/redemption/redeem``。

守卫只读取 ``alipay.enabled``，不接触私钥/公钥，测试不写入真实密钥。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend-web"))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps  # noqa: E402
from app.api.routes import (  # noqa: E402
    advertisements as ad_routes,
    billing as billing_routes,
    payment as payment_routes,
    redemption as redemption_routes,
)
from app.services.alipay_guard import AlipayDisabledError  # noqa: E402


class DisabledSession:
    """模拟 ``alipay.enabled`` 缺失：scalar 查询一律返回 None → 守卫视为关闭。"""

    async def scalar(self, _statement):
        return None

    async def execute(self, _statement):
        return SimpleNamespace(
            scalar_one_or_none=lambda: None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
        )

    async def commit(self):
        return None

    async def flush(self):
        return None


def _user(is_admin: bool = False) -> SimpleNamespace:
    role = "admin" if is_admin else "user"
    return SimpleNamespace(id=7, role=role, is_admin=is_admin)


def _build_app(session) -> FastAPI:
    """构建挂载 billing/payment/advertisements/redemption 路由的最小应用。

    覆盖数据库会话为 ``DisabledSession``（alipay 关闭），覆盖认证依赖返回普通用户。
    """
    app = FastAPI()
    app.include_router(billing_routes.router, prefix="/api/v1")
    app.include_router(payment_routes.router, prefix="/api/v1")
    app.include_router(ad_routes.router, prefix="/api/v1/advertisements")
    app.include_router(redemption_routes.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_active_user] = lambda: _user()
    app.dependency_overrides[deps.get_current_admin_user] = lambda: _user(is_admin=True)
    app.dependency_overrides[deps.get_db_session] = lambda: session
    return app


class AlipayGuardedBillingTests(unittest.TestCase):
    """套餐支付入口默认关闭。"""

    def test_order_pay_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        resp = client.post("/api/v1/billing/orders/NO1/pay")
        self.assertEqual(resp.status_code, 503)

    def test_payment_readiness_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        resp = client.get("/api/v1/billing/payment-readiness")
        self.assertEqual(resp.status_code, 503)

    def test_create_billing_order_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        resp = client.post(
            "/api/v1/billing/orders",
            json={"product_type": "plan", "product_id": 1, "request_key": "reqkey-12"},
        )
        self.assertEqual(resp.status_code, 503)

    def test_get_billing_order_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        resp = client.get("/api/v1/billing/orders/NO1")
        self.assertEqual(resp.status_code, 503)


class AlipayGuardedRechargeTests(unittest.TestCase):
    """余额充值入口默认关闭。"""

    def test_recharge_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        resp = client.post("/api/v1/payment/recharge", json={"amount": "10.00"})
        self.assertEqual(resp.status_code, 503)

    def test_recharge_status_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        resp = client.get("/api/v1/payment/recharge/NO1")
        self.assertEqual(resp.status_code, 503)


class AlipayGuardedAdTests(unittest.TestCase):
    """广告付款入口默认关闭。"""

    def test_ad_pay_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        resp = client.post("/api/v1/advertisements/1/pay")
        self.assertEqual(resp.status_code, 503)

    def test_ad_pay_notify_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        resp = client.post("/api/v1/advertisements/1/pay/notify?order_no=NO1")
        self.assertEqual(resp.status_code, 503)


class AlipayNotifyCallbackTests(unittest.TestCase):
    """支付宝异步回调默认关闭：不验签、不入账，直接 503。

    回调处理器内部自建会话（无注入依赖），故 patch ``require_alipay_enabled``
    使其抛 ``AlipayDisabledError``，验证处理器转换为 503 且不进入业务逻辑。
    """

    def test_payment_alipay_notify_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        with patch(
            "app.api.routes.payment.require_alipay_enabled",
            new=AsyncMock(side_effect=AlipayDisabledError("支付未开通")),
        ):
            resp = client.post(
                "/api/v1/payment/alipay/notify", data={"out_trade_no": "NO1"}
            )
        self.assertEqual(resp.status_code, 503)

    def test_billing_alipay_notify_returns_503(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        with patch(
            "app.api.routes.billing.require_alipay_enabled",
            new=AsyncMock(side_effect=AlipayDisabledError("支付未开通")),
        ):
            resp = client.post(
                "/api/v1/billing/alipay/notify", data={"out_trade_no": "NO1"}
            )
        self.assertEqual(resp.status_code, 503)


class AlipayGuardExemptTests(unittest.TestCase):
    """兑换码、套餐目录、权益、提现、结算不受支付宝总开关影响。"""

    def test_catalog_still_works(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        with patch(
            "app.api.routes.billing.BillingService.list_catalog",
            new=AsyncMock(return_value={"plans": [], "ai_quota_packages": []}),
        ):
            resp = client.get("/api/v1/billing/catalog")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertIn("plans", body["data"])
        self.assertIn("ai_quota_packages", body["data"])

    def test_entitlements_still_works(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        with patch(
            "app.api.routes.billing.SubscriptionFeatureService.get_entitlements",
            new=AsyncMock(
                return_value={
                    "plan_code": "free",
                    "account_limit": 1,
                    "monthly_ai_quota": 0,
                    "features": [],
                    "expires_at": None,
                    "source": "free_fallback",
                }
            ),
        ):
            resp = client.get("/api/v1/billing/entitlements")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_withdraw_still_works(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        with patch(
            "app.api.routes.payment.SettlementService.create_withdraw_record",
            new=AsyncMock(
                return_value={"success": True, "message": "ok", "data": {"id": 1}}
            ),
        ):
            resp = client.post("/api/v1/payment/withdraw", json={"amount": "10.00"})
        self.assertEqual(resp.status_code, 200)

    def test_settlement_records_still_works(self):
        app = _build_app(DisabledSession())
        client = TestClient(app)
        with patch(
            "app.api.routes.payment.SettlementService.get_settlement_records",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "data": {
                        "list": [],
                        "total": 0,
                        "page": 1,
                        "page_size": 20,
                        "total_pages": 0,
                    },
                }
            ),
        ):
            resp = client.get("/api/v1/payment/settlement-records")
        self.assertEqual(resp.status_code, 200)

    def test_redeem_still_works_and_no_full_code(self):
        from common.services.redemption_service import RedeemResult

        app = _build_app(DisabledSession())
        client = TestClient(app)
        canned = RedeemResult(
            record_id=101,
            code_id=202,
            batch_id=303,
            product_type="plan",
            product_code="standard",
            grant_id=404,
            ledger_id=505,
            action="new",
            duplicate=False,
        )
        with patch(
            "app.api.routes.redemption.RedemptionService.redeem",
            new=AsyncMock(return_value=canned),
        ):
            resp = client.post(
                "/api/v1/redemption/redeem",
                json={
                    "code": "abcdefgh",
                    "idempotency_key": "idempkey1",
                    "confirm": True,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        data = body["data"]
        self.assertEqual(data["record_id"], 101)
        # 响应不回显完整兑换码
        self.assertNotIn("code", data)
        self.assertNotIn("codes", data)
        self.assertNotIn("code_digest", data)


if __name__ == "__main__":
    unittest.main()
