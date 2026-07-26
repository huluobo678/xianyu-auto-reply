"""兑换码 API 路由。

客户接口：
- ``POST /api/v1/redemption/redeem``：核销兑换码，发放套餐或 AI 加量额度。
  请求体含 ``code`` 与 ``idempotency_key``，支持 ``confirm`` 用于升级二次确认；
  防重复提交，不在响应/异常/日志中回显完整兑换码。

管理员接口（``get_current_admin_user`` 保护，普通用户 403），挂在 ``/admin`` 下：
- ``POST /admin/redemption/batches``：生成并提交兑换码批次，仅返回批次元数据；
- ``GET /admin/redemption/batches``：查询批次列表（仅尾4位/状态/统计，不含完整码）；
- ``POST /admin/redemption/batches/{id}/export``：一次性导出完整兑换码到临时文件；
- ``POST /admin/redemption/batches/{id}/disable``：禁用批次；
- ``POST /admin/redemption/codes/{id}/disable``：禁用单码；
- ``GET /admin/redemption/audit``：兑换审计（不含完整兑换码）。

安全：完整兑换码只通过一次性导出交付；导出文件写入仓库外临时目录，
下载/超时后删除；日志与审计只记尾4位/ID/状态，绝不记录完整码。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from common.models.user import User
from common.schemas.common import ApiResponse
from common.services.redemption_service import (
    RedemptionError,
    RedemptionService,
)
from common.utils.time_utils import safe_isoformat

logger = logging.getLogger(__name__)

# 客户路由：仅暴露 /redeem（POST /api/v1/redemption/redeem）
router = APIRouter(prefix="/redemption", tags=["兑换码"])

# 管理员路由：挂在 /admin 下，最终路径为 /api/v1/admin/redemption/*
# 所有端点使用 get_current_admin_user，普通用户访问返回 403。
admin_router = APIRouter(prefix="/redemption", tags=["兑换码-管理"])

# 升级场景前端二次确认弹窗文案
_UPGRADE_CONFIRM_MESSAGE = "旧套餐剩余时间将不折算、不退补，确认继续兑换吗？"


class RedeemRequest(BaseModel):
    code: str = Field(min_length=8, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=64)
    # 升级场景二次确认：前端弹窗确认后置 true 才真正发起兑换
    confirm: bool = False


class CreateBatchRequest(BaseModel):
    product_type: str = Field(pattern="^(plan|ai_quota_package)$")
    product_code: str = Field(min_length=1, max_length=64)
    # 套餐码：monthly/quarterly（不允许 yearly）；加量包：忽略，传任意非空串
    billing_cycle: str | None = Field(default=None)
    count: int = Field(gt=0, le=10000)
    expires_at: datetime | None = None


def _redeem_success_message(action: str, duplicate: bool) -> str:
    if duplicate:
        return "该兑换请求已处理过，权益未重复发放"
    mapping = {
        "new": "兑换成功，套餐已开通",
        "renew": "续期成功，有效期已延长",
        "upgrade": "升级成功，新套餐已立即生效（旧套餐剩余时间不折算）",
        "pending_downgrade": "降级已记录为待生效，当前套餐到期后自动降级",
        "package": "加量包兑换成功",
    }
    return mapping.get(action, "兑换成功")


def _serialize_redeem_result(result) -> dict:
    return {
        "record_id": result.record_id,
        "batch_id": result.batch_id,
        "product_type": result.product_type,
        "product_code": result.product_code,
        "action": result.action,
        "duplicate": result.duplicate,
    }


@router.post("/redeem", response_model=ApiResponse)
async def redeem_code(
    payload: RedeemRequest,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """客户兑换兑换码。

    - 防重复提交：``idempotency_key`` + 服务层行锁/唯一约束保证幂等；
    - 升级场景：``confirm=false`` 时只做只读预览，返回 ``needs_confirmation=true``
      让前端弹窗确认；``confirm=true`` 时真正核销；
    - 不在响应/异常/日志中回显完整兑换码（错误信息均为通用文案）。
    """
    # 只读预览：检测升级场景需要前端二次确认（不消耗兑换码）
    if not payload.confirm:
        from common.db.session import async_session_maker

        async with async_session_maker() as preview_session:
            try:
                preview = await RedemptionService(preview_session).preview_redeem(
                    current_user.id, payload.code
                )
            except RedemptionError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if preview.get("action") == "upgrade":
            return ApiResponse(
                success=False,
                message=_UPGRADE_CONFIRM_MESSAGE,
                data={
                    "needs_confirmation": True,
                    "action": "upgrade",
                    "code_last4": preview.get("code_last4"),
                    "product_type": preview.get("product_type"),
                    "product_code": preview.get("product_code"),
                },
            )

    try:
        result = await RedemptionService(session).redeem(
            current_user.id, payload.code, payload.idempotency_key
        )
    except RedemptionError as exc:
        # RedemptionError 文案均为通用提示，不含完整兑换码
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(
        success=True,
        message=_redeem_success_message(result.action, result.duplicate),
        data=_serialize_redeem_result(result),
    )


# ==================== 管理员接口（admin_router，挂在 /admin 下） ====================


@admin_router.post("/batches", response_model=ApiResponse)
async def create_batch(
    payload: CreateBatchRequest,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """原子创建兑换码批次并提交，仅返回批次元数据。"""
    if payload.product_type == "plan":
        if payload.billing_cycle not in ("monthly", "quarterly"):
            raise HTTPException(
                status_code=400, detail="套餐兑换码只支持月卡或季卡，不支持年卡"
            )
        cycle_or_validity = payload.billing_cycle
    else:
        cycle_or_validity = payload.billing_cycle or "package"
    try:
        result = await RedemptionService(session).create_batch(
            admin_id=current_user.id,
            product_type=payload.product_type,
            product_code=payload.product_code,
            cycle_or_validity=cycle_or_validity,
            count=payload.count,
            expires_at=payload.expires_at,
        )
        await session.commit()
    except RedemptionError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        logging.getLogger(__name__).exception("创建兑换码批次事务提交失败")
        raise HTTPException(status_code=500, detail="兑换码批次创建失败") from exc
    return ApiResponse(
        success=True,
        message="批次已创建，请立即执行一次性导出。",
        data={
            "batch_id": result.batch_id,
            "batch_no": result.batch_no,
            "product_type": result.product_type,
            "product_code": result.product_code,
        },
    )


@admin_router.get("/batches", response_model=ApiResponse)
async def list_batches(
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
    limit: int = Query(100, ge=1, le=500),
) -> ApiResponse:
    """查询兑换码批次列表（仅尾4位/状态/统计，不含完整码）。"""
    batches = await RedemptionService(session).list_batches(limit=limit)
    return ApiResponse(success=True, data=batches)


@admin_router.post("/batches/{batch_id}/export")
async def export_batch(
    batch_id: int,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> FileResponse:
    """一次性导出某批次完整兑换码到临时文件并下载。

    - 仅未导出的批次可导出一次；成功后标记 ``exported_at``；
    - 导出文件写入专用临时目录（已 gitignore），下载后由回调删除；
    - 不得把导出文件提交 Git。
    """
    file_path: str | None = None
    try:
        file_path = await RedemptionService(session).export_batch_once(batch_id)
        await session.commit()
    except RedemptionError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        if file_path:
            RedemptionService.delete_export_file(file_path)
        logging.getLogger(__name__).exception("一次性导出事务提交失败")
        raise HTTPException(status_code=500, detail="兑换码导出失败，请重试") from exc
    file_name = Path(file_path).name
    # 下载完成后删除临时文件，避免完整兑换码驻留磁盘
    background = BackgroundTasks()
    background.add_task(RedemptionService.delete_export_file, file_path)
    return FileResponse(
        file_path,
        media_type="text/plain",
        filename=file_name,
        background=background,
    )


@admin_router.post("/batches/{batch_id}/disable", response_model=ApiResponse)
async def disable_batch(
    batch_id: int,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
    reason: str | None = Query(default=None, max_length=200),
) -> ApiResponse:
    """禁用兑换码批次。"""
    try:
        await RedemptionService(session).disable_batch(batch_id, reason)
    except RedemptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return ApiResponse(success=True, message="批次已禁用")


@admin_router.post("/codes/{code_id}/disable", response_model=ApiResponse)
async def disable_code(
    code_id: int,
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
    reason: str | None = Query(default=None, max_length=200),
) -> ApiResponse:
    """禁用单个兑换码。"""
    try:
        await RedemptionService(session).disable_code(code_id, reason)
    except RedemptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return ApiResponse(success=True, message="兑换码已禁用")


@admin_router.get("/audit", response_model=ApiResponse)
async def audit(
    current_user: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
    batch_id: int | None = Query(default=None, ge=1),
    limit: int = Query(100, ge=1, le=500),
) -> ApiResponse:
    """兑换审计查询（不含完整兑换码）。"""
    records = await RedemptionService(session).audit(batch_id=batch_id, limit=limit)
    # 标准化时间字段，便于前端展示
    for item in records:
        if item.get("created_at") is not None:
            item["created_at"] = safe_isoformat(item["created_at"])
    return ApiResponse(success=True, data=records)
