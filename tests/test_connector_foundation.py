import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from connector.cloud_client import ConnectorCloudClient, ConnectorCloudError
from connector.protocol import DeliveryState, MessageEnvelope, can_transition
from connector.secure_store import SecureCredentialStore
from connector.recovery import runtime_is_active, should_auto_recover
from connector.pairing import (
    OFFICIAL_SAAS_URL,
    PairingCancelled,
    PairingTimeout,
    run_pairing,
)
from connector.xianyu_runtime import MAX_RECONNECT_FAILURES
from common.utils.sensitive_logging import REDACTED, redact_sensitive_text
from connector.updater import ConnectorUpdateError, download_installer, version_is_newer
from connector.xianyu_runtime import LocalXianyuRuntime
from connector.xianyu_token import _requires_verification
from common.models.connector import (
    ConnectorDevice,
    ConnectorPairingSession,
    ConnectorReleaseVersion,
)


def test_connector_models_use_isolated_tables():
    assert ConnectorDevice.__tablename__ == "xy_connector_devices"
    assert ConnectorReleaseVersion.__tablename__ == "xy_connector_release_versions"


def test_auto_recovery_requires_complete_local_and_device_credentials():
    complete = {
        "server_url": "https://xy.example.com",
        "device_id": "1",
        "device_token": "device-secret",
        "xianyu": {
            "account_id": "123",
            "cookies": "unb=123; cookie=secret",
            "token": "xianyu-secret",
        },
    }
    assert should_auto_recover(complete, manually_stopped=False)
    assert not should_auto_recover(complete, manually_stopped=True)
    for key in ("device_token", "server_url"):
        assert not should_auto_recover({**complete, key: ""}, manually_stopped=False)
    for key in ("account_id", "cookies", "token"):
        incomplete = {**complete, "xianyu": {**complete["xianyu"], key: ""}}
        assert not should_auto_recover(incomplete, manually_stopped=False)


def test_runtime_active_guard_prevents_duplicate_start():
    from concurrent.futures import Future

    future = Future()
    assert runtime_is_active(future)
    future.set_result(None)
    assert not runtime_is_active(future)
    assert not runtime_is_active(None)


def test_runtime_reconnect_attempts_are_bounded():
    assert MAX_RECONNECT_FAILURES == 5


def test_sensitive_logging_redacts_secrets_and_verification_urls():
    message = (
        "Authorization: Bearer auth-secret Cookie: unb=1; _m_h5_tk=token "
        "password=hunter2 device_token=device-secret secret_key=remote-secret "
        "slider verification URL: https://verify.example.com/path?token=query-secret"
    )
    redacted = redact_sensitive_text(message)
    for secret in (
        "auth-secret",
        "unb=1",
        "hunter2",
        "device-secret",
        "remote-secret",
        "verify.example.com",
        "query-secret",
    ):
        assert secret not in redacted
    assert REDACTED in redacted


def test_delivery_state_rejects_duplicate_send():
    assert can_transition(DeliveryState.PENDING_SEND, DeliveryState.SENT)
    assert not can_transition(DeliveryState.SENT, DeliveryState.PENDING_SEND)


def test_message_envelope_defaults_to_protocol_v1():
    envelope = MessageEnvelope("r", "d", "a", "m", "i", 1)
    assert envelope.protocol_version == 1


def test_cloud_payload_rejects_xianyu_secrets():
    with pytest.raises(ConnectorCloudError):
        ConnectorCloudClient._assert_safe_payload({"accounts": [{"cookies": "secret"}]})
    with pytest.raises(ConnectorCloudError):
        ConnectorCloudClient._assert_safe_payload({"token": "secret"})
    ConnectorCloudClient._assert_safe_payload(
        {"accounts": [{"account_id": "123", "connection_status": "connected"}]}
    )


def test_cloud_url_requires_https_except_localhost():
    ConnectorCloudClient("http://127.0.0.1:8000").validate_base_url()
    ConnectorCloudClient("https://saas.example.com").validate_base_url()
    with pytest.raises(ConnectorCloudError):
        ConnectorCloudClient("http://saas.example.com").validate_base_url()


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_secure_store_uses_dpapi_and_supports_nested_values(tmp_path):
    store = SecureCredentialStore(tmp_path / "credentials.bin")
    values = {
        "device_token": "device-secret",
        "xianyu": {"cookies": "cookie-secret", "token": "token-secret"},
    }
    store.save(values)
    encrypted = store.path.read_bytes()
    assert b"cookie-secret" not in encrypted
    assert b"token-secret" not in encrypted
    assert store.load() == values


def test_existing_qr_and_connection_cores_load():
    code = """
import json
from connector.core_loader import load_connection_manager_types, load_qr_login_manager_class
manager_type, state_type = load_connection_manager_types()
print(json.dumps({
    'qr': load_qr_login_manager_class().__name__,
    'connection': manager_type.__name__,
    'state': state_type.CONNECTED.value,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.splitlines()[0])
    assert payload == {
        "qr": "QRLoginManager",
        "connection": "ConnectionManager",
        "state": "connected",
    }


def test_token_verification_detection():
    assert _requires_verification({"ret": ["FAIL_SYS_USER_VALIDATE::????"]})
    assert not _requires_verification({"ret": ["SUCCESS::????"]})


def test_local_runtime_builds_existing_registration_protocol(monkeypatch):
    sent = []

    class FakeWebSocket:
        async def send(self, value):
            sent.append(json.loads(value))

    async def no_sleep(_seconds):
        return None

    runtime = LocalXianyuRuntime("unb=12345; _m_h5_tk=fake_1")
    runtime.current_token = "local-token"
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    asyncio.run(runtime._register(FakeWebSocket()))
    assert sent[0]["lwp"] == "/reg"
    assert sent[0]["headers"]["token"] == "local-token"
    assert sent[1]["lwp"] == "/r/SyncStatus/ackDiff"


def test_cloud_client_minimum_flow(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8")) if request.data else None
        requests.append((request.full_url, payload, timeout))
        if request.full_url.endswith("/auth/login"):
            return FakeResponse(
                {"success": True, "token": "saas-token", "refresh_token": "refresh"}
            )
        if request.full_url.endswith("/devices/register"):
            return FakeResponse(
                {
                    "success": True,
                    "data": {
                        "id": 7,
                        "device_name": "PC",
                        "device_token": "device-token",
                    },
                }
            )
        if request.full_url.endswith("/heartbeat"):
            return FakeResponse({"success": True, "data": {"status": "online"}})
        return FakeResponse({"success": True, "data": {"accounts": ["123"]}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ConnectorCloudClient("http://127.0.0.1:8000")
    login = client.login("user", "password")
    device = client.register_device(
        login["token"],
        {
            "device_uuid": "a" * 32,
            "device_name": "PC",
            "platform": "windows",
            "app_version": "0.2.0",
        },
    )
    client.heartbeat(device["id"], device["device_token"], account_count=1)
    client.sync_account_state(device["id"], device["device_token"], "123", "connected")
    state_payload = requests[-1][1]
    assert state_payload == {
        "accounts": [
            {
                "account_id": "123",
                "connection_status": "connected",
                "error_code": None,
                "error_message": None,
            }
        ]
    }


def test_server_state_schema_rejects_secret_fields():
    repository_root = Path(__file__).resolve().parents[1]
    code = """
from pydantic import ValidationError
from app.api.routes.connectors import StateSyncRequest
StateSyncRequest.model_validate({'accounts': [{'account_id': '123', 'connection_status': 'connected'}]})
try:
    StateSyncRequest.model_validate({'accounts': [{'account_id': '123', 'connection_status': 'connected', 'cookies': 'secret'}]})
except ValidationError:
    print('secret_rejected')
else:
    raise SystemExit('secret field was accepted')
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(repository_root / "backend-web"), str(repository_root)]
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "secret_rejected" in result.stdout


def test_phase3_message_schema_rejects_credentials():
    repository_root = Path(__file__).resolve().parents[1]
    code = """
from pydantic import ValidationError
from app.api.routes.connectors import ConnectorMessageRequest
base = {
    'global_message_id': 'xy:account:message-123456',
    'account_id': '123',
    'chat_id': 'chat-1',
    'sender_user_id': 'buyer-1',
    'message_text': '你好',
}
ConnectorMessageRequest.model_validate(base)
for field in ('cookies', 'token', 'password'):
    try:
        ConnectorMessageRequest.model_validate({**base, field: 'secret'})
    except ValidationError:
        continue
    raise SystemExit(f'{field} was accepted')
print('credentials_rejected')
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(repository_root / "backend-web"), str(repository_root)]
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "credentials_rejected" in result.stdout


def test_local_delivery_state_prevents_duplicate_reply(tmp_path):
    from connector.local_state import LocalDeliveryState

    database = tmp_path / "delivery.db"
    state = LocalDeliveryState(database)
    assert not state.is_sent("global-1")
    state.mark("global-1", "sending")
    assert not state.is_sent("global-1")
    state.mark("global-1", "sent")
    assert LocalDeliveryState(database).is_sent("global-1")


def test_phase3_cloud_message_and_send_result_flow(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requests.append((request.full_url, payload, request.headers))
        if request.full_url.endswith("/messages"):
            return FakeResponse(
                {
                    "success": True,
                    "data": {
                        "status": "reply_ready",
                        "should_reply": True,
                        "reply_content": "云端回复",
                    },
                }
            )
        return FakeResponse({"success": True, "data": {"status": "sent"}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = ConnectorCloudClient("http://127.0.0.1:8000")
    decision = client.process_message(
        7,
        "device-token-value-12345",
        {
            "global_message_id": "xy:123:message-123456",
            "account_id": "123",
            "chat_id": "chat-1",
            "sender_user_id": "buyer-1",
            "message_text": "你好",
        },
    )
    assert decision["reply_content"] == "云端回复"
    result = client.report_send_result(
        7,
        "device-token-value-12345",
        "xy:123:message-123456",
        {
            "send_result_id": "result-1234567890123456",
            "success": True,
        },
    )
    assert result["status"] == "sent"
    assert requests[0][0].endswith("/connectors/devices/7/messages")
    assert requests[1][0].endswith(
        "/connectors/devices/7/messages/xy:123:message-123456/send-result"
    )
    assert all(
        "cookie" not in json.dumps(payload).lower() for _, payload, _ in requests
    )


def test_ai_usage_supports_atomic_connector_settlement():
    from common.services.ai_usage_service import AIUsageService

    assert callable(AIUsageService.commit_locked)
    assert callable(AIUsageService.release_locked)


def test_connector_update_version_comparison():
    assert version_is_newer("0.2.0", "0.2.1")
    assert version_is_newer("0.2.9", "0.3.0")
    assert not version_is_newer("0.2.0", "0.2.0")
    assert not version_is_newer("0.3.0", "0.2.9")


def test_connector_update_download_verifies_sha256(monkeypatch, tmp_path):
    payload = b"signed-by-hash-not-certificate"

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            nonlocal payload
            chunk, payload = payload, b""
            return chunk

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse()
    )
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    import hashlib

    expected = hashlib.sha256(b"signed-by-hash-not-certificate").hexdigest()
    installer = download_installer("https://downloads.example.com/setup.exe", expected)
    assert installer.read_bytes() == b"signed-by-hash-not-certificate"


def test_connector_update_rejects_insecure_download():
    with pytest.raises(ConnectorUpdateError):
        download_installer("http://downloads.example.com/setup.exe", "0" * 64)


def test_cloud_client_device_release_lookup(monkeypatch):
    observed = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "success": True,
                    "data": {
                        "version": "0.2.1",
                        "download_url": "https://downloads.example.com/setup.exe",
                        "sha256": "a" * 64,
                        "mandatory": False,
                    },
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["token"] = request.headers["X-connector-token"]
        observed["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    release = ConnectorCloudClient("http://127.0.0.1:8000").latest_release(
        7, "device-token-value-12345"
    )
    assert release["version"] == "0.2.1"
    assert observed == {
        "url": "http://127.0.0.1:8000/api/v1/connectors/devices/7/release/latest",
        "token": "device-token-value-12345",
        "timeout": 15,
    }


def test_pairing_model_uses_hash_only_session_fields():
    assert ConnectorPairingSession.__tablename__ == "xy_connector_pairing_sessions"
    columns = set(ConnectorPairingSession.__table__.columns.keys())
    assert {
        "id",
        "token_hash",
        "state",
        "status",
        "expires_at",
        "approved_by_user_id",
        "device_uuid",
        "device_name",
        "platform",
        "app_version",
        "approved_at",
        "consumed_at",
        "created_at",
    } <= columns
    assert "pairing_token" not in columns
    assert "device_token" not in columns


def test_official_saas_url_is_fixed():
    assert OFFICIAL_SAAS_URL == "https://xy.yunshuzhilian.asia"


def test_pairing_flow_opens_browser_and_consumes_once():
    opened = []
    statuses = []

    class FakeClient:
        def create_pairing_session(self, metadata):
            assert metadata["platform"] == "windows"
            return {
                "pairing_id": "pair-1",
                "pairing_token": "secret-token",
                "authorization_url": "https://xy.example/pair",
            }

        def pairing_session_status(self, pairing_id, token):
            assert (pairing_id, token) == ("pair-1", "secret-token")
            return {"status": "approved"}

        def consume_pairing_session(self, pairing_id, token):
            return {"id": 7, "device_token": "device-secret"}

        def cancel_pairing_session(self, pairing_id, token):
            raise AssertionError("successful pairing must not be cancelled")

    result = run_pairing(
        FakeClient(),
        {"platform": "windows"},
        __import__("threading").Event(),
        open_browser=opened.append,
        on_status=statuses.append,
        poll_interval=0,
    )
    assert result == {"id": 7, "device_token": "device-secret"}
    assert opened == ["https://xy.example/pair"]
    assert statuses == ["等待浏览器登录并确认绑定…"]


def test_pairing_flow_cancel_stops_polling():
    cancelled = []
    event = __import__("threading").Event()
    event.set()

    class FakeClient:
        def create_pairing_session(self, metadata):
            return {
                "pairing_id": "pair-1",
                "pairing_token": "secret-token",
                "authorization_url": "https://xy.example/pair",
            }

        def cancel_pairing_session(self, pairing_id, token):
            cancelled.append((pairing_id, token))

        def pairing_session_status(self, pairing_id, token):
            raise AssertionError("cancelled pairing must not poll")

    with pytest.raises(PairingCancelled):
        run_pairing(
            FakeClient(), {}, event, open_browser=lambda _url: None, poll_interval=0
        )
    assert cancelled == [("pair-1", "secret-token")]


def test_pairing_flow_timeout_is_bounded():
    cancelled = []

    class FakeClient:
        def create_pairing_session(self, metadata):
            return {
                "pairing_id": "pair-1",
                "pairing_token": "secret-token",
                "authorization_url": "https://xy.example/pair",
            }

        def cancel_pairing_session(self, pairing_id, token):
            cancelled.append((pairing_id, token))

    with pytest.raises(PairingTimeout):
        run_pairing(
            FakeClient(),
            {},
            __import__("threading").Event(),
            open_browser=lambda _url: None,
            timeout_seconds=-1,
        )
    assert cancelled == [("pair-1", "secret-token")]


def test_pairing_cloud_calls_keep_secret_out_of_url(monkeypatch):
    calls = []
    client = ConnectorCloudClient("http://127.0.0.1:8000")

    def fake_request(path, **kwargs):
        calls.append((path, kwargs))
        return {"success": True, "data": {"status": "pending"}}

    monkeypatch.setattr(client, "_request", fake_request)
    client.pairing_session_status("pair-1", "pairing-secret")
    assert calls[0][0] == "/connectors/pairing-sessions/pair-1/status"
    assert "pairing-secret" not in calls[0][0]
    assert calls[0][1]["headers"] == {"X-Pairing-Token": "pairing-secret"}
