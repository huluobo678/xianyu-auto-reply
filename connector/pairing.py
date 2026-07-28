from __future__ import annotations

import threading
import time
import webbrowser
from collections.abc import Callable

from connector.cloud_client import ConnectorCloudClient

OFFICIAL_SAAS_URL = "https://xy.yunshuzhilian.asia"
TERMINAL_STATUSES = {"cancelled", "consumed", "expired"}


class PairingCancelled(RuntimeError):
    pass


class PairingTimeout(RuntimeError):
    pass


def run_pairing(
    client: ConnectorCloudClient,
    metadata: dict,
    cancel_event: threading.Event,
    *,
    open_browser: Callable[[str], object] = webbrowser.open,
    on_status: Callable[[str], None] | None = None,
    poll_interval: float = 3.0,
    timeout_seconds: float = 600.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    pairing = client.create_pairing_session(metadata)
    pairing_id = str(pairing["pairing_id"])
    pairing_token = str(pairing["pairing_token"])
    open_browser(str(pairing["authorization_url"]))
    deadline = time.monotonic() + timeout_seconds
    if on_status:
        on_status("等待浏览器登录并确认绑定…")
    while time.monotonic() < deadline:
        if cancel_event.is_set():
            client.cancel_pairing_session(pairing_id, pairing_token)
            raise PairingCancelled("配对已取消")
        status = str(
            client.pairing_session_status(pairing_id, pairing_token).get(
                "status", "pending"
            )
        )
        if status == "approved":
            return client.consume_pairing_session(pairing_id, pairing_token)
        if status in TERMINAL_STATUSES:
            raise PairingCancelled("配对会话已失效，请重试")
        sleep(poll_interval)
    client.cancel_pairing_session(pairing_id, pairing_token)
    raise PairingTimeout("配对已超时，请重试")
