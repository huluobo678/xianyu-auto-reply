from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from common.services.billing_service import BillingCatalogError, BillingService
from common.models.user import User
from common.schemas.common import ApiResponse

router = APIRouter(prefix='/billing', tags=['billing'])


class CreateBillingOrderRequest(BaseModel):
    product_type: str = Field(pattern='^(plan|ai_quota_package)$')
    product_id: int = Field(gt=0)
    request_key: str = Field(min_length=8, max_length=64)


@router.get('/catalog', response_model=ApiResponse)
async def get_billing_catalog(
    _: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    data = await BillingService(session).list_catalog()
    return ApiResponse(success=True, data=data)


@router.post('/orders', response_model=ApiResponse)
async def create_billing_order(
    payload: CreateBillingOrderRequest,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    try:
        order, duplicate = await BillingService(session).create_order(
            current_user.id, payload.product_type,
            payload.product_id, payload.request_key,
        )
    except BillingCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = BillingService.serialize_order(order)
    data.update({'duplicate': duplicate, 'payment_ready': False})
    return ApiResponse(
        success=True,
        message='Order created; payment channel is not enabled yet',
        data=data,
    )
