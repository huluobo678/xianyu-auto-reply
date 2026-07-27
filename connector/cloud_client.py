from __future__ import annotations

import json
import urllib.error
import urllib.request


class ConnectorCloudError(RuntimeError):
    pass


class ConnectorCloudClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(self, path: str, *, method: str, payload: dict | None = None, headers: dict | None = None) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = urllib.request.Request(f"{self.base_url}/api/v1{path}", data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ConnectorCloudError(f"???? HTTP {exc.code}: {detail[:200]}") from exc
        except urllib.error.URLError as exc:
            raise ConnectorCloudError(f"??????: {exc.reason}") from exc
        if not result.get("success"):
            raise ConnectorCloudError(result.get("message") or "??????")
        return result.get("data") or {}

    def register_device(self, access_token: str, payload: dict) -> dict:
        return self._request("/connectors/devices/register", method="POST", payload=payload, headers={"Authorization": f"Bearer {access_token}"})

    def heartbeat(self, device_id: int, device_token: str) -> dict:
        return self._request(f"/connectors/devices/{device_id}/heartbeat", method="POST", headers={"X-Connector-Token": device_token})
