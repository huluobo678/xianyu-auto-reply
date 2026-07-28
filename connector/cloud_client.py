from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from connector import __version__


class ConnectorCloudError(RuntimeError):
    pass


class ConnectorCloudClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def validate_base_url(self) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
            raise ConnectorCloudError("SaaS ?????? HTTPS???????? HTTP")

    @staticmethod
    def _assert_safe_payload(payload: dict | None) -> None:
        forbidden = {
            "cookie",
            "cookies",
            "cookie_value",
            "token",
            "access_token",
            "refresh_token",
        }
        stack = [payload] if payload else []
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                overlap = forbidden.intersection(key.lower() for key in value)
                if overlap:
                    raise ConnectorCloudError(f"??????????????{sorted(overlap)[0]}")
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    def _request(
        self,
        path: str,
        *,
        method: str,
        payload: dict | None = None,
        headers: dict | None = None,
        safe_payload: bool = True,
    ) -> dict:
        self.validate_base_url()
        if safe_payload:
            self._assert_safe_payload(payload)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"XianyuLocalConnector/{__version__}",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            f"{self.base_url}/api/v1{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("detail") or detail
            except json.JSONDecodeError:
                pass
            raise ConnectorCloudError(
                f"?????? HTTP {exc.code}: {str(detail)[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ConnectorCloudError(f"???? SaaS?{exc.reason}") from exc
        if result.get("success") is False:
            raise ConnectorCloudError(result.get("message") or "??????")
        return result

    def login(self, username: str, password: str) -> dict:
        result = self._request(
            "/auth/login",
            method="POST",
            payload={"username": username, "password": password},
            safe_payload=False,
        )
        if not result.get("token"):
            raise ConnectorCloudError(result.get("message") or "SaaS ????")
        return result

    def register_device_by_code(self, binding_code: str, payload: dict) -> dict:
        result = self._request(
            "/connectors/devices/register-by-code",
            method="POST",
            payload={**payload, "binding_code": binding_code},
        )
        return result.get("data") or {}
    def register_device(self, access_token: str, payload: dict) -> dict:
        result = self._request(
            "/connectors/devices/register",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return result.get("data") or {}

    def heartbeat(
        self,
        device_id: int,
        device_token: str,
        *,
        app_status: str = "online",
        account_count: int = 0,
    ) -> dict:
        result = self._request(
            f"/connectors/devices/{device_id}/heartbeat",
            method="POST",
            payload={"app_status": app_status, "account_count": account_count},
            headers={"X-Connector-Token": device_token},
        )
        return result.get("data") or {}

    def process_message(self, device_id: int, device_token: str, payload: dict) -> dict:
        result = self._request(
            f"/connectors/devices/{device_id}/messages",
            method="POST",
            payload=payload,
            headers={"X-Connector-Token": device_token},
        )
        return result.get("data") or {}

    def report_send_result(
        self,
        device_id: int,
        device_token: str,
        global_message_id: str,
        payload: dict,
    ) -> dict:
        result = self._request(
            f"/connectors/devices/{device_id}/messages/{global_message_id}/send-result",
            method="POST",
            payload=payload,
            headers={"X-Connector-Token": device_token},
        )
        return result.get("data") or {}

    def latest_release(self, device_id: int, device_token: str) -> dict | None:
        result = self._request(
            f"/connectors/devices/{device_id}/release/latest",
            method="GET",
            headers={"X-Connector-Token": device_token},
        )
        return result.get("data")
    def sync_account_state(
        self,
        device_id: int,
        device_token: str,
        account_id: str,
        state: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict:
        result = self._request(
            f"/connectors/devices/{device_id}/state",
            method="POST",
            payload={
                "accounts": [
                    {
                        "account_id": account_id,
                        "connection_status": state,
                        "error_code": error_code,
                        "error_message": error_message,
                    }
                ]
            },
            headers={"X-Connector-Token": device_token},
        )
        return result.get("data") or {}
