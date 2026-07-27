from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from common.models.connector import ConnectorAccountBinding, ConnectorDevice
from common.models.user import User
from common.models.xy_account import XYAccount
from common.schemas.common import ApiResponse
from common.utils.time_utils import get_beijing_now_naive

router = APIRouter(prefix="/connectors", tags=["local-connector"])


class RegisterDeviceRequest(BaseModel):
    device_uuid: str = Field(min_length=16, max_length=64)
    device_name: str = Field(min_length=1, max_length=120)
    platform: str = Field(default="windows", pattern="^windows$")
    app_version: str = Field(min_length=1, max_length=32)


class BindAccountRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=80)


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
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "revoked_at": device.revoked_at.isoformat() if device.revoked_at else None,
    }


async def _owned_device(session: AsyncSession, user_id: int, device_id: int) -> ConnectorDevice:
    device = await session.scalar(
        select(ConnectorDevice).where(ConnectorDevice.id == device_id, ConnectorDevice.user_id == user_id)
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Connector device not found")
    return device


@router.post("/devices/register", response_model=ApiResponse)
async def register_device(
    payload: RegisterDeviceRequest,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await session.scalar(select(ConnectorDevice).where(ConnectorDevice.device_uuid == payload.device_uuid))
    if device is not None and device.user_id != current_user.id:
        raise HTTPException(status_code=409, detail="Device is already registered to another user")
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
        select(ConnectorDevice).where(ConnectorDevice.user_id == current_user.id).order_by(ConnectorDevice.id.desc())
    )
    return ApiResponse(success=True, data=[_serialize_device(device) for device in result.scalars()])


@router.post("/devices/{device_id}/heartbeat", response_model=ApiResponse)
async def heartbeat(
    device_id: int,
    x_connector_token: str = Header(min_length=20),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    device = await session.get(ConnectorDevice, device_id)
    if device is None or device.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credential")
    if not hmac.compare_digest(device.credential_hash, _credential_hash(x_connector_token)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credential")
    device.last_seen_at = get_beijing_now_naive()
    await session.commit()
    return ApiResponse(success=True, data={"device_id": device.id, "status": "online"})


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
        select(XYAccount).where(XYAccount.account_id == payload.account_id, XYAccount.owner_id == current_user.id)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Xianyu account not found")
    binding = await session.scalar(
        select(ConnectorAccountBinding).where(ConnectorAccountBinding.account_id == payload.account_id)
    )
    if binding is None:
        binding = ConnectorAccountBinding(
            device_id=device.id,
            account_id=payload.account_id,
            owner_id=current_user.id,
        )
        session.add(binding)
    elif binding.owner_id != current_user.id:
        raise HTTPException(status_code=409, detail="Account is bound to another user device")
    else:
        binding.device_id = device.id
        binding.connection_status = "offline"
        binding.last_error_code = None
        binding.last_error_message = None
    await session.commit()
    return ApiResponse(success=True, data={"device_id": device.id, "account_id": payload.account_id})


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
        select(ConnectorAccountBinding).where(ConnectorAccountBinding.device_id == device.id)
    )
    for binding in result.scalars():
        binding.connection_status = "revoked"
    await session.commit()
    return ApiResponse(success=True, data=_serialize_device(device))
