from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from common.models.ai_usage import AIAccountMonthlyUsage, AIAccountQuotaConfig
from common.models.ai_usage import AIQuotaConfig, AIUsageRequest, AIUserMonthlyUsage
from common.models.billing import AIQuotaGrant
from common.models.user import User
from common.models.xy_account import XYAccount
from common.schemas.common import ApiResponse
from common.services.ai_usage_service import current_period_start
from common.services.subscription_feature_service import SubscriptionFeatureService
from common.utils.time_utils import get_beijing_now_naive

router = APIRouter(tags=["AI usage"])
admin_router = APIRouter(tags=["AI usage admin"])


class UserQuotaUpdate(BaseModel):
    package_quota: int = Field(default=0, ge=0)
    independent_quota: int = Field(default=0, ge=0)


class AccountQuotaUpdate(BaseModel):
    monthly_quota: int | None = Field(default=None, ge=0)
    requests_per_minute: int = Field(default=60, ge=1, le=10000)
    max_concurrency: int = Field(default=1, ge=1, le=1000)


@router.get("/ai-usage", response_model=ApiResponse)
async def get_my_ai_usage(
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    period = current_period_start()
    usage = await session.scalar(select(AIUserMonthlyUsage).where(
        AIUserMonthlyUsage.user_id == current_user.id,
        AIUserMonthlyUsage.period_start == period,
    ))
    config = await session.scalar(select(AIQuotaConfig).where(AIQuotaConfig.user_id == current_user.id))
    used = int(usage.effective_replies if usage else 0)
    entitlements = await SubscriptionFeatureService(session).get_entitlements(current_user.id)
    quota = int(entitlements["monthly_ai_quota"]) + int(config.independent_quota if config else 0)
    now = get_beijing_now_naive()
    active_grants = (
        AIQuotaGrant.user_id == current_user.id,
        AIQuotaGrant.grant_type.in_(("signup_bonus", "quota_package")),
        AIQuotaGrant.status == "active",
        AIQuotaGrant.starts_at <= now,
        or_(AIQuotaGrant.expires_at.is_(None), AIQuotaGrant.expires_at > now),
    )
    addon_remaining = int(await session.scalar(
        select(func.coalesce(func.sum(AIQuotaGrant.remaining_quota), 0)).where(*active_grants)
    ) or 0)
    addon_total = int(await session.scalar(
        select(func.coalesce(func.sum(AIQuotaGrant.total_quota), 0)).where(*active_grants)
    ) or 0)
    remaining = max(0, quota - used) + addon_remaining
    warning_quota = quota + addon_total
    warning = (
        100 if warning_quota > 0 and used >= warning_quota
        else 80 if warning_quota and used * 100 >= warning_quota * 80
        else None
    )
    message = "AI quota reached 100%" if warning == 100 else "AI quota reached 80%" if warning == 80 else "ok"
    return ApiResponse(
        success=True,
        message=message,
        data={"effective_replies": used, "remaining_quota": remaining},
    )


@admin_router.get("/ai-usage", response_model=ApiResponse)
async def admin_list_ai_usage(
    user_id: int | None = Query(default=None),
    account_id: str | None = Query(default=None),
    period: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    target_period = date(period.year, period.month, 1) if period else current_period_start()
    stmt = select(AIAccountMonthlyUsage, XYAccount).join(
        XYAccount, XYAccount.id == AIAccountMonthlyUsage.account_pk
    ).where(AIAccountMonthlyUsage.period_start == target_period)
    if user_id is not None:
        stmt = stmt.where(AIAccountMonthlyUsage.user_id == user_id)
    if account_id:
        stmt = stmt.where(XYAccount.account_id == account_id)
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await session.execute(stmt.order_by(
        AIAccountMonthlyUsage.effective_replies.desc()
    ).offset((page - 1) * page_size).limit(page_size))).all()
    items = []
    for usage, account in rows:
        tokens = (await session.execute(select(
            func.coalesce(func.sum(AIUsageRequest.input_tokens), 0),
            func.coalesce(func.sum(AIUsageRequest.output_tokens), 0),
        ).where(
            AIUsageRequest.account_pk == account.id,
            AIUsageRequest.period_start == target_period,
            AIUsageRequest.status == "committed",
        ))).one()
        items.append({
            "user_id": usage.user_id,
            "account_pk": account.id,
            "account_id": account.account_id,
            "account_name": account.display_name,
            "period": target_period.isoformat(),
            "effective_replies": int(usage.effective_replies),
            "reserved_replies": int(usage.reserved_replies),
            "input_tokens": int(tokens[0]),
            "output_tokens": int(tokens[1]),
            "estimated_cost": str(usage.estimated_cost or 0),
        })
    return ApiResponse(success=True, data={"items": items, "total": total, "page": page, "page_size": page_size})


@admin_router.get("/ai-usage/requests", response_model=ApiResponse)
async def admin_list_ai_usage_requests(
    user_id: int | None = Query(default=None),
    account_id: str | None = Query(default=None),
    period: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    target_period = date(period.year, period.month, 1) if period else current_period_start()
    stmt = select(AIUsageRequest).where(AIUsageRequest.period_start == target_period)
    if user_id is not None:
        stmt = stmt.where(AIUsageRequest.user_id == user_id)
    if account_id:
        stmt = stmt.where(AIUsageRequest.account_id == account_id)
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    requests = (await session.scalars(
        stmt.order_by(AIUsageRequest.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all()
    items = [{
        "request_id": item.id,
        "user_id": item.user_id,
        "account_pk": item.account_pk,
        "account_id": item.account_id,
        "period": item.period_start.isoformat(),
        "status": item.status,
        "model_name": item.model_name,
        "provider_name": item.provider_name,
        "requested_at": item.requested_at.isoformat() if item.requested_at else None,
        "latency_ms": item.latency_ms,
        "input_tokens": item.input_tokens,
        "output_tokens": item.output_tokens,
        "token_source": item.token_source,
        "estimated_cost": str(item.estimated_cost or 0),
        "auto_reply_log_id": item.auto_reply_log_id,
    } for item in requests]
    return ApiResponse(success=True, data={"items": items, "total": total, "page": page, "page_size": page_size})


@admin_router.put("/ai-quota/users/{user_id}", response_model=ApiResponse)
async def update_user_ai_quota(
    user_id: int,
    payload: UserQuotaUpdate,
    _: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    if await session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    config = await session.scalar(select(AIQuotaConfig).where(AIQuotaConfig.user_id == user_id))
    if config:
        config.package_quota = payload.package_quota
        config.independent_quota = payload.independent_quota
    else:
        session.add(AIQuotaConfig(
            user_id=user_id,
            package_quota=payload.package_quota,
            independent_quota=payload.independent_quota,
        ))
    await session.commit()
    return ApiResponse(success=True, message="AI quota updated")


@admin_router.put("/ai-quota/accounts/{account_pk}", response_model=ApiResponse)
async def update_account_ai_quota(
    account_pk: int,
    payload: AccountQuotaUpdate,
    _: User = Depends(deps.get_current_admin_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    if await session.get(XYAccount, account_pk) is None:
        raise HTTPException(status_code=404, detail="Account not found")
    config = await session.scalar(select(AIAccountQuotaConfig).where(AIAccountQuotaConfig.account_pk == account_pk))
    if not config:
        config = AIAccountQuotaConfig(account_pk=account_pk)
        session.add(config)
    config.monthly_quota = payload.monthly_quota
    config.requests_per_minute = payload.requests_per_minute
    config.max_concurrency = payload.max_concurrency
    await session.commit()
    return ApiResponse(success=True, message="AI account limits updated")
