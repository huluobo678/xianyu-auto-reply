from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from common.models.connector import (
    ConnectorAccountBinding,
    ConnectorBindingCode,
    ConnectorConversationControl,
    ConnectorDevice,
    ConnectorMessage,
    ConnectorReleaseVersion,
)
from common.models.user import User
from common.models.xy_account import XYAccount
from common.schemas.common import ApiResponse
from common.services.ai_usage_service import AIUsageService
from app.services.websocket_client import websocket_client
from common.utils.time_utils import get_beijing_now_naive

router = APIRouter(prefix="/connectors", tags=["local-connector"])


class RegisterDeviceRequest(BaseModel):
    device_uuid: str = Field(min_length=16, max_length=64)
    device_name: str = Field(min_length=1, max_length=120)
    platform: str = Field(default="windows", pattern="^windows$")
    app_version: str = Field(min_length=1, max_length=32)


class RegisterDeviceByCodeRequest(RegisterDeviceRequest):
    binding_code: str = Field(min_length=20, max_length=128)

    model_config = ConfigDict(extra="forbid")

class BindAccountRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=80)


class HeartbeatRequest(BaseModel):
    app_status: str = Field(default="online", pattern="^(online|degraded|stopping)$")
    account_count: int = Field(default=0, ge=0, le=50)

    model_config = ConfigDict(extra="forbid")


class AccountStateRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=80)
    connection_status: str = Field(
        pattern="^(offline|connecting|connected|reconnecting|verification_required|error|stopped)$"
    )
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid")


class StateSyncRequest(BaseModel):
    accounts: list[AccountStateRequest] = Field(max_length=50)

    model_config = ConfigDict(extra="forbid")


class ConnectorMessageRequest(BaseModel):
    global_message_id: str = Field(min_length=16, max_length=191)
    account_id: str = Field(min_length=1, max_length=80)
    source_message_id: str | None = Field(default=None, max_length=128)
    chat_id: str = Field(min_length=1, max_length=128)
    sender_user_id: str = Field(min_length=1, max_length=128)
    sender_user_name: str = Field(default="", max_length=120)
    message_text: str = Field(min_length=1, max_length=10000)
    item_id: str | None = Field(default=None, max_length=64)
    msg_time: str = Field(default="", max_length=64)
    sender_is_self: bool = False

    model_config = ConfigDict(extra="forbid")


class SendResultRequest(BaseModel):
    send_result_id: str = Field(min_length=16, max_length=191)
    success: bool
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid")


class ReplaceDeviceRequest(BaseModel):
    new_device_id: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")

def _credential_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _serialize_device(device: ConnectorDevice) -> dict:
    return {
        "id": device.id,
        "device_uuid": device.device_uuid,
        "device_name": device.device_name,
        "platform": device.platform,
        "app_version": device.app_version,
        "status": device.status,
        "last_seen_at": device.last_seen_at.isoformat()
        if device.last_seen_at
        else None,
        "app_status": device.app_status,
        "last_error_code": device.last_error_code,
        "last_error_message": device.last_error_message,
        "revoked_at": device.revoked_at.isoformat() if device.revoked_at else None,
    }


async def _owned_device(
    session: AsyncSession, user_id: int, device_id: int
) -> ConnectorDevice:
    device = await session.scalar(
        select(ConnectorDevice).where(
            ConnectorDevice.id == device_id, ConnectorDevice.user_id == user_id
        )
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Connector device not found")
    return device


async def _authenticated_device(
    session: AsyncSession,
    device_id: int,
    device_token: str,
) -> ConnectorDevice:
    device = await session.get(ConnectorDevice, device_id)
    if device is None or device.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credential"
        )
    if not hmac.compare_digest(device.credential_hash, _credential_hash(device_token)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credential"
        )
    return device


def _serialize_message(message: ConnectorMessage, *, duplicate: bool = False) -> dict:
    return {
        "global_message_id": message.global_message_id,
        "status": message.status,
        "should_reply": message.status == "reply_ready",
        "reply_content": message.reply_content,
        "reply_strategy": message.reply_strategy or "none",
        "reply_mode": message.reply_mode or "none",
        "decision_reason": message.decision_reason,
        "duplicate": duplicate,
    }


async def _bound_account(session: AsyncSession, device: ConnectorDevice, account_id: str) -> ConnectorAccountBinding:
    binding = await session.scalar(
        select(ConnectorAccountBinding).where(
            ConnectorAccountBinding.device_id == device.id,
            ConnectorAccountBinding.account_id == account_id,
            ConnectorAccountBinding.owner_id == device.user_id,
        )
    )
    if binding is None:
        raise HTTPException(status_code=409, detail="Account is not bound to this device")
    return binding


@router.post("/binding-codes", response_model=ApiResponse)
async def create_binding_code(
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    code = secrets.token_urlsafe(24)
    now = get_beijing_now_naive()
    session.add(ConnectorBindingCode(
        user_id=current_user.id,
        code_hash=_credential_hash(code),
        expires_at=now + timedelta(minutes=10),
    ))
    await session.commit()
    return ApiResponse(success=True, data={"binding_code": code, "expires_in_seconds": 600})


@router.post("/devices/register-by-code", response_model=ApiResponse)
async def register_device_by_code(
    payload: RegisterDeviceByCodeRequest,
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    now = get_beijing_now_naive()
    binding_code = await session.scalar(
        select(ConnectorBindingCode)
        .where(
            ConnectorBindingCode.code_hash == _credential_hash(payload.binding_code),
            ConnectorBindingCode.used_at.is_(None),
            ConnectorBindingCode.expires_at > now,
        )
        .with_for_update()
    )
    if binding_code is None:
        raise HTTPException(status_code=401, detail="Invalid or expired binding code")
    device = await session.scalar(select(ConnectorDevice).where(ConnectorDevice.device_uuid == payload.device_uuid))
    if device is not None and device.user_id != binding_code.user_id:
        raise HTTPException(status_code=409, detail="Device is already registered to another user")
    token = secrets.token_urlsafe(32)
    if device is None:
        device = ConnectorDevice(
            user_id=binding_code.user_id, device_uuid=payload.device_uuid,
            device_name=payload.device_name, platform=payload.platform,
            app_version=payload.app_version, credential_hash=_credential_hash(token),
        )
        session.add(device)
    else:
        device.device_name = payload.device_name
        device.app_version = payload.app_version
        device.status = "active"
        device.revoked_at = None
        device.credential_version += 1
        device.credential_hash = _credential_hash(token)
    binding_code.used_at = now
    await session.commit()
    await session.refresh(device)
    data = _serialize_device(device)
    data["device_token"] = token
    return ApiResponse(success=True, data=data)

@router.post("/devices/register", response_model=ApiResponse)
async def register_device(
    payload: RegisterDeviceRequest,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await session.scalar(
        select(ConnectorDevice).where(
            ConnectorDevice.device_uuid == payload.device_uuid
        )
    )
    if device is not None and device.user_id != current_user.id:
        raise HTTPException(
            status_code=409, detail="Device is already registered to another user"
        )
    token = secrets.token_urlsafe(32)
    if device is None:
        device = ConnectorDevice(
            user_id=current_user.id,
            device_uuid=payload.device_uuid,
            device_name=payload.device_name,
            platform=payload.platform,
            app_version=payload.app_version,
            credential_hash=_credential_hash(token),
        )
        session.add(device)
    else:
        device.device_name = payload.device_name
        device.platform = payload.platform
        device.app_version = payload.app_version
        device.status = "active"
        device.revoked_at = None
        device.credential_version += 1
        device.credential_hash = _credential_hash(token)
    await session.commit()
    await session.refresh(device)
    data = _serialize_device(device)
    data["device_token"] = token
    return ApiResponse(success=True, data=data)


@router.get("/devices", response_model=ApiResponse)
async def list_devices(
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    result = await session.execute(
        select(ConnectorDevice)
        .where(ConnectorDevice.user_id == current_user.id)
        .order_by(ConnectorDevice.id.desc())
    )
    return ApiResponse(
        success=True, data=[_serialize_device(device) for device in result.scalars()]
    )


@router.post("/devices/{device_id}/heartbeat", response_model=ApiResponse)
async def heartbeat(
    device_id: int,
    payload: HeartbeatRequest | None = None,
    x_connector_token: str = Header(min_length=20),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await _authenticated_device(session, device_id, x_connector_token)
    device.last_seen_at = get_beijing_now_naive()
    device.app_status = payload.app_status if payload else "online"
    device.last_error_code = None
    device.last_error_message = None
    await session.commit()
    return ApiResponse(
        success=True,
        data={
            "device_id": device.id,
            "status": "online",
            "app_status": payload.app_status if payload else "online",
            "account_count": payload.account_count if payload else 0,
        },
    )


@router.post("/devices/{device_id}/state", response_model=ApiResponse)
async def sync_state(
    device_id: int,
    payload: StateSyncRequest,
    x_connector_token: str = Header(min_length=20),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await _authenticated_device(session, device_id, x_connector_token)
    now = get_beijing_now_naive()
    device.last_seen_at = now
    synced = []
    for account_state in payload.accounts:
        binding = await session.scalar(
            select(ConnectorAccountBinding).where(
                ConnectorAccountBinding.account_id == account_state.account_id
            )
        )
        if binding is None:
            binding = ConnectorAccountBinding(
                device_id=device.id,
                account_id=account_state.account_id,
                owner_id=device.user_id,
            )
            session.add(binding)
        elif binding.owner_id != device.user_id:
            raise HTTPException(
                status_code=409, detail="Account is bound to another user device"
            )
        else:
            binding.device_id = device.id
        binding.connection_status = account_state.connection_status
        binding.last_error_code = account_state.error_code
        binding.last_error_message = account_state.error_message
        if account_state.connection_status == "connected":
            binding.last_connected_at = now
        synced.append(account_state.account_id)
    await session.commit()
    return ApiResponse(success=True, data={"device_id": device.id, "accounts": synced})


@router.post("/devices/{device_id}/bindings", response_model=ApiResponse)
async def bind_account(
    device_id: int,
    payload: BindAccountRequest,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await _owned_device(session, current_user.id, device_id)
    if device.status != "active":
        raise HTTPException(status_code=409, detail="Device has been revoked")
    account = await session.scalar(
        select(XYAccount).where(
            XYAccount.account_id == payload.account_id,
            XYAccount.owner_id == current_user.id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Xianyu account not found")
    binding = await session.scalar(
        select(ConnectorAccountBinding).where(
            ConnectorAccountBinding.account_id == payload.account_id
        )
    )
    if binding is None:
        binding = ConnectorAccountBinding(
            device_id=device.id,
            account_id=payload.account_id,
            owner_id=current_user.id,
        )
        session.add(binding)
    elif binding.owner_id != current_user.id:
        raise HTTPException(
            status_code=409, detail="Account is bound to another user device"
        )
    else:
        binding.device_id = device.id
        binding.connection_status = "offline"
        binding.last_error_code = None
        binding.last_error_message = None
    await session.commit()
    return ApiResponse(
        success=True, data={"device_id": device.id, "account_id": payload.account_id}
    )


@router.delete("/devices/{device_id}/bindings/{account_id}", response_model=ApiResponse)
async def unbind_account(
    device_id: int,
    account_id: str,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    await _owned_device(session, current_user.id, device_id)
    binding = await session.scalar(
        select(ConnectorAccountBinding).where(
            ConnectorAccountBinding.device_id == device_id,
            ConnectorAccountBinding.account_id == account_id,
            ConnectorAccountBinding.owner_id == current_user.id,
        )
    )
    if binding is not None:
        await session.delete(binding)
        await session.commit()
    return ApiResponse(success=True)


@router.post("/devices/{device_id}/revoke", response_model=ApiResponse)
async def revoke_device(
    device_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await _owned_device(session, current_user.id, device_id)
    device.status = "revoked"
    device.revoked_at = get_beijing_now_naive()
    device.credential_hash = secrets.token_hex(32)
    result = await session.execute(
        select(ConnectorAccountBinding).where(
            ConnectorAccountBinding.device_id == device.id
        )
    )
    for binding in result.scalars():
        binding.connection_status = "revoked"
    await session.commit()
    return ApiResponse(success=True, data=_serialize_device(device))

@router.post("/devices/{device_id}/messages", response_model=ApiResponse)
async def process_connector_message(
    device_id: int,
    payload: ConnectorMessageRequest,
    x_connector_token: str = Header(min_length=20),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await _authenticated_device(session, device_id, x_connector_token)
    binding = await _bound_account(session, device, payload.account_id)
    existing = await session.scalar(
        select(ConnectorMessage).where(
            ConnectorMessage.global_message_id == payload.global_message_id
        )
    )
    if existing is not None and existing.status != "decision_failed":
        return ApiResponse(success=True, data=_serialize_message(existing, duplicate=True))

    now = get_beijing_now_naive()
    if existing is None:
        message = ConnectorMessage(
            global_message_id=payload.global_message_id,
            device_id=device.id,
            owner_id=device.user_id,
            account_id=payload.account_id,
            source_message_id=payload.source_message_id,
            chat_id=payload.chat_id,
            sender_user_id=payload.sender_user_id,
            sender_user_name=payload.sender_user_name or None,
            item_id=payload.item_id,
            message_text=payload.message_text,
            sender_is_self=payload.sender_is_self,
        )
        session.add(message)
        binding.last_message_at = now
        device.last_seen_at = now
        try:
            await session.commit()
            await session.refresh(message)
        except IntegrityError:
            await session.rollback()
            message = await session.scalar(
                select(ConnectorMessage).where(
                    ConnectorMessage.global_message_id == payload.global_message_id
                )
            )
            if message is None:
                raise
            return ApiResponse(success=True, data=_serialize_message(message, duplicate=True))
    else:
        message = existing

    control = await session.scalar(
        select(ConnectorConversationControl).where(
            ConnectorConversationControl.account_id == payload.account_id,
            ConnectorConversationControl.chat_id == payload.chat_id,
        )
    )
    account = await session.scalar(
        select(XYAccount).where(
            XYAccount.account_id == payload.account_id,
            XYAccount.owner_id == device.user_id,
        )
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Xianyu account not found")

    if payload.sender_is_self:
        pause_minutes = max(1, int(account.pause_duration or 10))
        if control is None:
            control = ConnectorConversationControl(
                owner_id=device.user_id,
                account_id=payload.account_id,
                chat_id=payload.chat_id,
            )
            session.add(control)
        control.manual_takeover_until = now + timedelta(minutes=pause_minutes)
        control.reason = "self_message"
        message.status = "no_reply"
        message.decision_reason = "manual_takeover"
        message.decided_at = now
        await session.commit()
        return ApiResponse(success=True, data=_serialize_message(message))

    if control is not None and control.manual_takeover_until and control.manual_takeover_until > now:
        message.status = "no_reply"
        message.decision_reason = "manual_takeover"
        message.decided_at = now
        await session.commit()
        return ApiResponse(success=True, data=_serialize_message(message))

    decision_response = await websocket_client.decide_connector_reply(
        {
            "global_message_id": payload.global_message_id,
            "account_id": payload.account_id,
            "source_message_id": payload.source_message_id,
            "chat_id": payload.chat_id,
            "sender_user_id": payload.sender_user_id,
            "sender_user_name": payload.sender_user_name,
            "message_text": payload.message_text,
            "item_id": payload.item_id,
            "msg_time": payload.msg_time,
        }
    )
    if not decision_response.get("success"):
        message.status = "decision_failed"
        message.decision_reason = "cloud_decision_unavailable"
        message.decided_at = now
        await session.commit()
        raise HTTPException(status_code=503, detail="Cloud reply decision unavailable; auto reply paused")

    decision = decision_response.get("data") or {}
    message.decision_reason = str(decision.get("decision_reason") or "no_rule_matched")[:64]
    message.reply_strategy = str(decision.get("reply_strategy") or "none")[:24]
    message.reply_mode = str(decision.get("reply_mode") or "none")[:16]
    message.reply_content = decision.get("reply_content")
    message.ai_usage_request_id = decision.get("ai_usage_request_id")
    message.decided_at = now
    message.status = "reply_ready" if decision.get("should_reply") and message.reply_content else "no_reply"
    await session.commit()
    return ApiResponse(success=True, data=_serialize_message(message))


@router.post("/devices/{device_id}/messages/{global_message_id}/send-result", response_model=ApiResponse)
async def report_connector_send_result(
    device_id: int,
    global_message_id: str,
    payload: SendResultRequest,
    x_connector_token: str = Header(min_length=20),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await _authenticated_device(session, device_id, x_connector_token)
    message = await session.scalar(
        select(ConnectorMessage)
        .where(
            ConnectorMessage.global_message_id == global_message_id,
            ConnectorMessage.device_id == device.id,
        )
        .with_for_update()
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Connector message not found")
    if message.status in {"sent", "send_failed"}:
        return ApiResponse(success=True, data=_serialize_message(message, duplicate=True))
    if message.status != "reply_ready":
        raise HTTPException(status_code=409, detail="Message has no pending reply")

    if payload.success:
        if message.ai_usage_request_id:
            settled = await AIUsageService.commit_locked(session, message.ai_usage_request_id)
            if not settled:
                await session.rollback()
                raise HTTPException(status_code=409, detail="AI quota reservation cannot be committed")
        message.status = "sent"
        message.sent_at = get_beijing_now_naive()
        message.send_error_code = None
        message.send_error_message = None
    else:
        if message.ai_usage_request_id:
            await AIUsageService.release_locked(
                session, message.ai_usage_request_id, payload.error_code or "send_failed"
            )
        message.status = "send_failed"
        message.send_error_code = payload.error_code
        message.send_error_message = payload.error_message
    message.send_result_id = payload.send_result_id
    await session.commit()
    return ApiResponse(success=True, data=_serialize_message(message))


@router.get("/status", response_model=ApiResponse)
async def connector_status(
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    now = get_beijing_now_naive()
    devices_result = await session.execute(
        select(ConnectorDevice)
        .where(ConnectorDevice.user_id == current_user.id)
        .order_by(ConnectorDevice.id.desc())
    )
    bindings_result = await session.execute(
        select(ConnectorAccountBinding).where(
            ConnectorAccountBinding.owner_id == current_user.id
        )
    )
    bindings_by_device: dict[int, list[dict]] = {}
    for binding in bindings_result.scalars():
        bindings_by_device.setdefault(binding.device_id, []).append(
            {
                "account_id": binding.account_id,
                "connection_status": binding.connection_status,
                "last_connected_at": binding.last_connected_at.isoformat() if binding.last_connected_at else None,
                "last_message_at": binding.last_message_at.isoformat() if binding.last_message_at else None,
                "error_code": binding.last_error_code,
                "error_message": binding.last_error_message,
            }
        )
    devices = []
    for device in devices_result.scalars():
        data = _serialize_device(device)
        data["online"] = bool(
            device.status == "active"
            and device.last_seen_at
            and now - device.last_seen_at <= timedelta(seconds=90)
            and device.app_status in {"online", "degraded"}
        )
        data["accounts"] = bindings_by_device.get(device.id, [])
        devices.append(data)
    return ApiResponse(success=True, data={"devices": devices})


@router.post("/devices/{device_id}/replace", response_model=ApiResponse)
async def replace_device(
    device_id: int,
    payload: ReplaceDeviceRequest,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    old_device = await _owned_device(session, current_user.id, device_id)
    new_device = await _owned_device(session, current_user.id, payload.new_device_id)
    if new_device.status != "active":
        raise HTTPException(status_code=409, detail="New device is not active")
    bindings = await session.execute(
        select(ConnectorAccountBinding).where(
            ConnectorAccountBinding.device_id == old_device.id,
            ConnectorAccountBinding.owner_id == current_user.id,
        )
    )
    moved_accounts = []
    for binding in bindings.scalars():
        binding.device_id = new_device.id
        binding.connection_status = "offline"
        binding.last_error_code = "device_replaced"
        binding.last_error_message = "等待新设备本地连接器上线"
        moved_accounts.append(binding.account_id)
    old_device.status = "revoked"
    old_device.app_status = "offline"
    old_device.revoked_at = get_beijing_now_naive()
    old_device.credential_hash = secrets.token_hex(32)
    await session.commit()
    return ApiResponse(
        success=True,
        data={"old_device_id": old_device.id, "new_device_id": new_device.id, "accounts": moved_accounts},
    )

@router.get("/release/latest", response_model=ApiResponse)
async def latest_connector_release(
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    release = await session.scalar(
        select(ConnectorReleaseVersion)
        .where(ConnectorReleaseVersion.platform == "windows-x64")
        .order_by(ConnectorReleaseVersion.published_at.desc())
    )
    if release is None:
        return ApiResponse(success=True, data=None, message="安装包尚未发布")
    return ApiResponse(success=True, data={
        "version": release.version,
        "download_url": release.download_url,
        "sha256": release.sha256,
        "mandatory": release.mandatory,
        "published_at": release.published_at.isoformat(),
    })


@router.get("/devices/{device_id}/release/latest", response_model=ApiResponse)
async def latest_connector_release_for_device(
    device_id: int,
    x_connector_token: str = Header(min_length=20),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await _authenticated_device(session, device_id, x_connector_token)
    release = await session.scalar(
        select(ConnectorReleaseVersion)
        .where(ConnectorReleaseVersion.platform == "windows-x64")
        .order_by(ConnectorReleaseVersion.published_at.desc())
    )
    if release is None:
        return ApiResponse(success=True, data=None, message="???????")
    return ApiResponse(
        success=True,
        data={
            "version": release.version,
            "download_url": release.download_url,
            "sha256": release.sha256,
            "mandatory": release.mandatory,
            "published_at": release.published_at.isoformat(),
            "device_id": device.id,
        },
    )
