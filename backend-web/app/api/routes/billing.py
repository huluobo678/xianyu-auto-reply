from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.services.billing_payment_service import (
    BillingPaymentError,
    BillingPaymentNotConfigured,
    BillingPaymentService,
)
from common.models.user import User
from common.schemas.common import ApiResponse
from common.services.billing_service import BillingCatalogError, BillingService

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


@router.get('/payment-readiness', response_model=ApiResponse)
async def get_payment_readiness(
    _: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    ready = await BillingPaymentService(session).payment_ready()
    return ApiResponse(success=True, data={'payment_ready': ready})


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
    payment_ready = await BillingPaymentService(session).payment_ready()
    data = BillingService.serialize_order(order)
    data.update({'duplicate': duplicate, 'payment_ready': payment_ready})
    message = 'Order ready for payment' if payment_ready else 'Order created; payment channel is not configured'
    return ApiResponse(success=True, message=message, data=data)


@router.post('/orders/{order_no}/pay', response_model=ApiResponse)
async def create_order_payment(
    order_no: str,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    service = BillingPaymentService(session)
    try:
        data = await service.create_alipay_payment(current_user.id, order_no)
    except BillingPaymentNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except BillingPaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(success=True, message='Alipay QR code created', data=data)


@router.get('/orders/{order_no}', response_model=ApiResponse)
async def get_billing_order(
    order_no: str,
    current_user: User = Depends(deps.get_current_active_user),
    session: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    payment_service = BillingPaymentService(session)
    order = await payment_service.get_order(current_user.id, order_no)
    if not order:
        raise HTTPException(status_code=404, detail='Billing order not found')
    data = BillingService.serialize_order(order)
    data.update(payment_service.serialize_payment(
        order, payment_ready=await payment_service.payment_ready()
    ))
    return ApiResponse(success=True, data=data)


@router.post('/alipay/notify')
async def billing_alipay_notify(request: Request) -> PlainTextResponse:
    from common.db.session import async_session_maker

    form_data = await request.form()
    notify_data = {key: value for key, value in form_data.items()}
    async with async_session_maker() as session:
        ok = await BillingPaymentService(session).handle_alipay_notify(notify_data)
    return PlainTextResponse('success' if ok else 'failure')
