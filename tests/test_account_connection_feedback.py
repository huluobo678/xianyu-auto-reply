from __future__ import annotations

import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = REPO_ROOT / "websocket/app/services/xianyu"
PACKAGE_NAME = "_xianyu_connection_feedback_tests"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(SERVICE_ROOT)]
sys.modules[PACKAGE_NAME] = package

for module_name in ("utils", "cookie_manager", "cookie_token_manager"):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE_NAME}.{module_name}",
        SERVICE_ROOT / f"{module_name}.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载测试模块: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

cookie_manager_module = sys.modules[f"{PACKAGE_NAME}.cookie_manager"]
CookieManager = cookie_manager_module.CookieManager
build_public_connection_status = cookie_manager_module.build_public_connection_status
CookieTokenManager = sys.modules[f"{PACKAGE_NAME}.cookie_token_manager"].CookieTokenManager


class _Task:
    def __init__(self, done: bool = False):
        self._done = done

    def done(self) -> bool:
        return self._done


class _State:
    def __init__(self, value: str):
        self.value = value


class _ConnectionManager:
    def __init__(self, state: str):
        self.connection_state = _State(state)


class _Instance:
    def __init__(self, state: str, token_status: str):
        self.connection_manager = _ConnectionManager(state)
        self.last_token_refresh_status = token_status
        self.last_connection_error_code = None
        self.last_connection_error_message = None


class _CooldownParent:
    def __init__(self):
        self.cookie_id = "account-1"
        self.risk_control_cooldown_until = time.time() + 60
        self.last_token_refresh_status = "failed_captcha"
        self.last_connection_error_code = None
        self.last_connection_error_message = None


class RiskControlCooldownBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_token_short_circuits_during_cooldown(self):
        parent = _CooldownParent()
        manager = CookieTokenManager(parent)

        result = await manager.refresh_token()

        self.assertIsNone(result)
        self.assertEqual(parent.last_token_refresh_status, "risk_control_cooldown")
        self.assertEqual(
            parent.last_connection_error_code,
            "xianyu_risk_control_required",
        )


class AccountConnectionFeedbackTests(unittest.TestCase):
    def test_connected_state_hides_stale_error(self):
        result = build_public_connection_status(
            "connected",
            "failed_captcha_max_retries",
            "xianyu_risk_control_required",
            "stale",
        )

        self.assertEqual(result["connection_status"], "online")
        self.assertIsNone(result["connection_error_code"])
        self.assertIsNone(result["connection_error_message"])

    def test_risk_control_failure_requires_attention(self):
        result = build_public_connection_status(
            "disconnected",
            "failed_captcha_max_retries",
        )

        self.assertEqual(result["connection_status"], "attention_required")
        self.assertEqual(
            result["connection_error_code"],
            "xianyu_risk_control_required",
        )
        self.assertIn("安全验证未通过", result["connection_error_message"])

    def test_risk_control_cooldown_requires_attention(self):
        result = build_public_connection_status(
            "disconnected",
            "risk_control_cooldown",
        )

        self.assertEqual(result["connection_status"], "attention_required")
        self.assertIn("暂停自动重试", result["connection_error_message"])

    def test_connection_stats_exposes_safe_account_status(self):
        manager = CookieManager()
        manager.tasks["account-1"] = _Task()
        manager.instances["account-1"] = _Instance(
            "disconnected",
            "failed_captcha",
        )

        stats = manager.get_connection_stats()
        status = stats["account_statuses"]["account-1"]

        self.assertEqual(stats["connected"], 0)
        self.assertEqual(status["connection_status"], "attention_required")
        self.assertNotIn("cookie", status)
        self.assertNotIn("token", status)

    def test_not_started_status_has_complete_contract(self):
        manager = CookieManager()

        status = manager.get_task_status("missing")

        self.assertEqual(status["connection_status"], "offline")
        self.assertEqual(status["connection_state"], "not_started")
        self.assertFalse(status["running"])

    def test_sensitive_values_are_not_logged(self):
        sources = [
            REPO_ROOT / "websocket/app/services/xianyu/cookie_token_manager.py",
            REPO_ROOT / "websocket/app/api/routes/internal.py",
            REPO_ROOT / "websocket/app/api/routes/password_login.py",
        ]
        forbidden = [
            "缓存Token: {cached_token}",
            "新Token: {new_token}",
            "Token刷新响应: {json.dumps",
            "[续期前全量Cookies]",
            "[续期后全量Cookies]",
            "[密码登录获取的新Cookies]",
            "滑块验证返回的全部cookies",
            "人脸认证验证链接已保存: {verification_url}",
        ]

        combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, combined)

    def test_qr_login_does_not_claim_immediate_connection_success(self):
        source = (REPO_ROOT / "backend-web/app/api/routes/qr_login.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("登录信息已保存，正在建立连接", source)
        self.assertNotIn('message="扫码登录成功"', source)

    def test_risk_control_failure_starts_cooldown(self):
        source = (
            REPO_ROOT / "websocket/app/services/xianyu/cookie_token_manager.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def _start_risk_control_cooldown", source)
        self.assertIn('self.last_token_refresh_status = "risk_control_cooldown"', source)
        self.assertGreaterEqual(source.count("self._start_risk_control_cooldown()"), 3)

    def test_manual_recheck_is_owner_scoped_and_reuses_restart(self):
        source = (REPO_ROOT / "backend-web/app/api/routes/cookies.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('@router.post("/{account_id}/recheck-connection"', source)
        self.assertIn(
            "account = await _get_account_or_404(current_user, account_id, account_service)",
            source,
        )
        self.assertIn('connection_status in {"connecting", "verifying"}', source)
        self.assertIn("await websocket_client.restart_account(account_id)", source)

    def test_frontend_exposes_completed_verification_recheck(self):
        api_source = (REPO_ROOT / "frontend/src/api/accounts.ts").read_text(
            encoding="utf-8"
        )
        page_source = (
            REPO_ROOT / "frontend/src/pages/accounts/Accounts.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("recheckAccountConnection", api_source)
        self.assertIn("${id}/recheck-connection", api_source)
        self.assertIn("已完成验证，重新检测", page_source)


if __name__ == "__main__":
    unittest.main()
