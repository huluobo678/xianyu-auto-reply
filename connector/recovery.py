from __future__ import annotations

from concurrent.futures import Future
from typing import Any


def has_complete_device_credentials(credentials: dict[str, Any]) -> bool:
    return all(
        str(credentials.get(key) or "").strip()
        for key in ("server_url", "device_id", "device_token")
    )


def has_complete_xianyu_credentials(credentials: dict[str, Any]) -> bool:
    account = credentials.get("xianyu") or {}
    return all(
        str(account.get(key) or "").strip()
        for key in ("account_id", "cookies", "token")
    )


def should_auto_recover(credentials: dict[str, Any], *, manually_stopped: bool) -> bool:
    return (
        not manually_stopped
        and has_complete_device_credentials(credentials)
        and has_complete_xianyu_credentials(credentials)
    )


def runtime_is_active(runtime_future: Future | None) -> bool:
    return runtime_future is not None and not runtime_future.done()
