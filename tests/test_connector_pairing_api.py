from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend-web"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import connectors as routes  # noqa: E402
from common.models.connector import ConnectorPairingSession  # noqa: E402
from common.utils.time_utils import get_beijing_now_naive  # noqa: E402


class FakeSession:
    def __init__(self, scalar_values=()):
        self.scalar_values = list(scalar_values)
        self.added = []
        self.commits = 0

    async def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        if getattr(value, "id", None) is None:
            value.id = 7


def test_create_pairing_session_persists_only_token_hash(monkeypatch):
    async def allow_rate_limit(_request):
        return None

    monkeypatch.setattr(routes, "_pairing_rate_limit", allow_rate_limit)
    session = FakeSession()
    response = asyncio.run(
        routes.create_pairing_session(
            routes.CreatePairingSessionRequest(
                device_uuid="device-uuid-123456",
                device_name="Test PC",
                platform="windows",
                app_version="0.3.0",
            ),
            SimpleNamespace(),
            session,
        )
    )
    pairing = session.added[0]
    token = response.data["pairing_token"]
    assert pairing.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token != pairing.token_hash
    assert response.data["authorization_url"].endswith(f"state={pairing.state}")
    assert token not in response.data["authorization_url"]


def test_approved_pairing_cannot_be_claimed_by_another_user():
    pairing = ConnectorPairingSession(
        id="pair-1",
        token_hash="hash",
        state="state-1",
        status="pending",
        expires_at=get_beijing_now_naive() + timedelta(minutes=5),
        device_uuid="device-uuid-123456",
        device_name="Test PC",
        platform="windows",
        app_version="0.3.0",
    )
    first_session = FakeSession([pairing])
    response = asyncio.run(
        routes.approve_pairing_session(
            "state-1", SimpleNamespace(id=101), first_session
        )
    )
    assert response.data == {"status": "approved"}
    assert pairing.approved_by_user_id == 101

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.approve_pairing_session(
                "state-1", SimpleNamespace(id=202), FakeSession([pairing])
            )
        )
    assert exc_info.value.status_code == 409


def test_pairing_credentials_are_consumed_once_and_replay_is_rejected():
    pairing_token = "pairing-token-secret"
    pairing = ConnectorPairingSession(
        id="pair-1",
        token_hash=routes._credential_hash(pairing_token),
        state="state-1",
        status="approved",
        expires_at=get_beijing_now_naive() + timedelta(minutes=5),
        approved_by_user_id=101,
        device_uuid="device-uuid-123456",
        device_name="Test PC",
        platform="windows",
        app_version="0.3.0",
        approved_at=get_beijing_now_naive(),
    )
    session = FakeSession([pairing, None])
    response = asyncio.run(
        routes.consume_pairing_session("pair-1", pairing_token, session)
    )
    device = session.added[0]
    device_token = response.data["device_token"]
    assert pairing.status == "consumed"
    assert pairing.consumed_at is not None
    assert device.credential_hash == routes._credential_hash(device_token)
    assert device_token != device.credential_hash

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes.consume_pairing_session(
                "pair-1", pairing_token, FakeSession([pairing])
            )
        )
    assert exc_info.value.status_code == 409


def test_expired_pairing_is_terminal():
    pairing = ConnectorPairingSession(
        id="pair-1",
        token_hash="hash",
        state="state-1",
        status="pending",
        expires_at=get_beijing_now_naive() - timedelta(seconds=1),
        device_uuid="device-uuid-123456",
        device_name="Test PC",
        platform="windows",
        app_version="0.3.0",
    )
    assert routes._expire_pairing(pairing)
    assert pairing.status == "expired"
