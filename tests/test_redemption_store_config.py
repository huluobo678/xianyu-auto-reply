"""兑换码商城地址配置测试（阶段三）。

覆盖 ``redemption_store_url`` 系统设置：

- 严格 HTTPS 与 host 白名单校验（拒绝 http、非 pay.ldxp.cn、userinfo、显式端口绕过）；
- 登录用户可读取、未登录不可读取；
- 非管理员不能修改（路由级 ``get_current_admin_user`` 守卫，返回 403）；
- 默认值正确（``alipay.enabled=false``、商城地址默认值）。

不写入支付宝密钥；不读取/输出 .env。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend-web"))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps  # noqa: E402
from app.api.routes import system_settings as ss_routes  # noqa: E402
from app.api.routes.system_settings import _validate_redemption_store_url  # noqa: E402
from app.services.system_setting_service import DEFAULT_SYSTEM_SETTINGS  # noqa: E402

DEFAULT_STORE_URL = "https://pay.ldxp.cn/shop/5LWTNV9V"


class StoreUrlValidationTests(unittest.TestCase):
    """``_validate_redemption_store_url`` 白名单与绕过防护。"""

    def test_valid_url_accepted(self):
        self.assertIsNone(_validate_redemption_store_url(DEFAULT_STORE_URL))
        self.assertIsNone(
            _validate_redemption_store_url("https://pay.ldxp.cn/shop/any")
        )

    def test_empty_rejected(self):
        self.assertIsNotNone(_validate_redemption_store_url(""))
        self.assertIsNotNone(_validate_redemption_store_url("   "))

    def test_http_rejected(self):
        self.assertIsNotNone(
            _validate_redemption_store_url("http://pay.ldxp.cn/shop/x")
        )

    def test_wrong_host_rejected(self):
        self.assertIsNotNone(_validate_redemption_store_url("https://evil.com/shop/x"))
        # 子域名 / look-alike 也应拒绝
        self.assertIsNotNone(
            _validate_redemption_store_url("https://pay.ldxp.cn.evil.com/x")
        )
        self.assertIsNotNone(_validate_redemption_store_url("https://notpay.ldxp.cn/x"))

    def test_userinfo_bypass_rejected(self):
        # userinfo 形式：host 实际为 evil.com，必须拒绝
        self.assertIsNotNone(
            _validate_redemption_store_url("https://pay.ldxp.cn@evil.com/shop/x")
        )
        self.assertIsNotNone(
            _validate_redemption_store_url("https://user:pass@pay.ldxp.cn/shop/x")
        )

    def test_explicit_port_rejected(self):
        # 即便 host 仍为 pay.ldxp.cn，显式端口也拒绝，避免端口绕过
        self.assertIsNotNone(
            _validate_redemption_store_url("https://pay.ldxp.cn:8080/shop/x")
        )

    def test_arbitrary_scheme_rejected(self):
        self.assertIsNotNone(_validate_redemption_store_url("javascript:pay.ldxp.cn"))
        self.assertIsNotNone(_validate_redemption_store_url("ftp://pay.ldxp.cn/x"))


class DefaultSettingsTests(unittest.TestCase):
    """默认系统设置含商城地址与支付宝总开关默认关闭。"""

    def test_redemption_store_url_default(self):
        value, _desc = DEFAULT_SYSTEM_SETTINGS["redemption_store_url"]
        self.assertEqual(value, DEFAULT_STORE_URL)

    def test_alipay_enabled_default_false(self):
        value, _desc = DEFAULT_SYSTEM_SETTINGS["alipay.enabled"]
        self.assertEqual(value, "false")


class FakeSettingService:
    """记录写操作、返回默认设置快照的最小服务替身。"""

    def __init__(self) -> None:
        self.settings = {
            key: value for key, (value, _desc) in DEFAULT_SYSTEM_SETTINGS.items()
        }
        self.set_calls: list[tuple] = []

    async def ensure_default_settings(self) -> None:
        return None

    async def list_settings(self, include_sensitive: bool = False) -> dict:
        return dict(self.settings)

    async def set_setting(self, key: str, value: str, description=None) -> None:
        self.set_calls.append((key, value, description))
        self.settings[key] = value


def _build_app(service: FakeSettingService, *, admin_user=None) -> FastAPI:
    app = FastAPI()
    app.include_router(ss_routes.router, prefix="/api/v1/system-settings")

    if admin_user is None:
        admin_user = SimpleNamespace(id=1, role="admin", is_admin=True)

    app.dependency_overrides[deps.get_current_active_user] = lambda: SimpleNamespace(
        id=7, role="user", is_admin=False
    )
    app.dependency_overrides[deps.get_current_admin_user] = lambda: admin_user
    app.dependency_overrides[deps.get_system_setting_service] = lambda: service
    return app


class StoreReadAccessTests(unittest.TestCase):
    """登录可读、未登录不可读。"""

    def test_logged_in_user_can_read_store_url(self):
        app = _build_app(FakeSettingService())
        client = TestClient(app)
        resp = client.get("/api/v1/system-settings")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body.get("redemption_store_url"), DEFAULT_STORE_URL)

    def test_public_cannot_read_store_url(self):
        # /public 无需登录，但白名单不含 redemption_store_url
        app = _build_app(FakeSettingService())
        client = TestClient(app)
        resp = client.get("/api/v1/system-settings/public")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertNotIn("redemption_store_url", body)


class StoreWriteTests(unittest.TestCase):
    """管理员可改、严格校验、非管理员 403。"""

    def test_admin_can_save_valid_url(self):
        service = FakeSettingService()
        app = _build_app(service)
        client = TestClient(app)
        resp = client.put(
            "/api/v1/system-settings/redemption_store_url",
            json={"value": "https://pay.ldxp.cn/shop/new"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        self.assertTrue(
            any(
                call[0] == "redemption_store_url"
                and call[1] == "https://pay.ldxp.cn/shop/new"
                for call in service.set_calls
            )
        )

    def test_non_https_rejected(self):
        service = FakeSettingService()
        app = _build_app(service)
        client = TestClient(app)
        resp = client.put(
            "/api/v1/system-settings/redemption_store_url",
            json={"value": "http://pay.ldxp.cn/shop/x"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(service.set_calls, [])

    def test_wrong_host_rejected(self):
        service = FakeSettingService()
        app = _build_app(service)
        client = TestClient(app)
        resp = client.put(
            "/api/v1/system-settings/redemption_store_url",
            json={"value": "https://evil.com/shop/x"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(service.set_calls, [])

    def test_port_bypass_rejected(self):
        service = FakeSettingService()
        app = _build_app(service)
        client = TestClient(app)
        resp = client.put(
            "/api/v1/system-settings/redemption_store_url",
            json={"value": "https://pay.ldxp.cn:8080/shop/x"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(service.set_calls, [])

    def test_non_admin_cannot_modify(self):
        # 路由级 get_current_admin_user 对非管理员抛 403
        def _deny():
            raise HTTPException(status_code=403, detail="仅管理员可修改该设置")

        service = FakeSettingService()
        app = FastAPI()
        app.include_router(ss_routes.router, prefix="/api/v1/system-settings")
        app.dependency_overrides[deps.get_current_admin_user] = _deny
        app.dependency_overrides[deps.get_system_setting_service] = lambda: service
        client = TestClient(app)
        resp = client.put(
            "/api/v1/system-settings/redemption_store_url",
            json={"value": "https://pay.ldxp.cn/shop/new"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(service.set_calls, [])


if __name__ == "__main__":
    unittest.main()
