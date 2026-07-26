"""
系统设置路由

功能：
1. 提供系统设置的读取与更新接口
2. 针对日志保留天数（log.retention_days）提供实时生效：
   - 保存成功后立即刷新当前 backend-web 进程的日志保留策略
   - 通过内部 HTTP 通知 websocket / scheduler 服务刷新
   - 未通知成功的服务由各自启动的自动同步任务兜底补齐
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.api import deps
from app.core.config import get_settings
from app.core.http_client import get_http_client
from common.models.user import User, UserRole
from common.schemas.common import ApiResponse
from common.schemas.system_setting import SystemSettingUpdate
from app.services.system_setting_service import SystemSettingService
from common.utils.logging_utils import update_log_retention
from common.utils.browser_utils import is_frozen

router = APIRouter(tags=["system_settings"])

# 日志保留天数的设置键，与前端保持一致
LOG_RETENTION_KEY = "log.retention_days"

NON_ADMIN_ALLOWED_KEYS = {
    "disclaimer.title",
    "disclaimer.content",
    "disclaimer.checkbox_text",
    "disclaimer.agree_button_text",
    "disclaimer.disagree_button_text",
    "login.system_name",
    "login.system_title",
    "login.system_description",
    "auth.footer_ad_html",
    "theme.effect",
    "theme.color_preset",
    "theme.font_family",
    "navigation.hidden_menu_keys",
    "distribution.fee_type",
    "distribution.fee_rate",
    "withdraw.min_amount",
    "runtime.is_exe_mode",
    # 普通用户需读取续期单价以在个人设置中计算续期总价
    "user.renew_month_price",
    # 兑换码商城地址：登录用户可读取（用于商城 iframe 嵌入），未登录不可读
    "redemption_store_url",
}


# 兑换码商城地址允许的协议与 host（严格白名单，拒绝任意域名绕过）
_REDEMPTION_STORE_ALLOWED_HOST = "pay.ldxp.cn"


def _validate_redemption_store_url(raw_value: str) -> str | None:
    """校验 redemption_store_url：必须 https 且 host 严格等于 pay.ldxp.cn。

    返回 None 表示校验通过，返回字符串表示错误提示。
    """
    from urllib.parse import urlsplit

    value = str(raw_value or "").strip()
    if not value:
        return "兑换码商城地址不能为空"
    parts = urlsplit(value)
    if parts.scheme != "https":
        return "兑换码商城地址必须使用 https"
    host = (parts.hostname or "").lower()
    if host != _REDEMPTION_STORE_ALLOWED_HOST:
        return "兑换码商城地址只允许 pay.ldxp.cn"
    # 拒绝用户信息 / 显式端口 / 查询串中夹带任意域名的绕过形态：
    # 合法商城地址无端口、无 userinfo；显式端口（如 pay.ldxp.cn:8080）
    # 即便 host 仍为 pay.ldxp.cn 也拒绝，避免端口绕过。
    if parts.username or parts.password:
        return "兑换码商城地址格式不合法"
    if parts.port is not None:
        return "兑换码商城地址不允许指定端口"
    return None


# 仅供管理员切换的敏感系统设置键：普通用户即使绕过前端也无法修改
_ADMIN_ONLY_KEYS = {
    "alipay.enabled",
    "alipay.app_id",
    "alipay.private_key",
    "alipay.alipay_public_key",
    "alipay.gateway_url",
    "alipay.notify_url",
    "alipay.billing_notify_url",
    "alipay.seller_id",
}


def _parse_log_retention_days(raw_value: str) -> tuple[int | None, str | None]:
    """解析日志保留天数输入，范围 1~365，返回 (天数, 错误提示)。"""
    value = str(raw_value or "").strip()
    if not value.isdigit():
        return None, "日志保留天数必须为1到365之间的整数"

    retention_days = int(value)
    if not (1 <= retention_days <= 365):
        return None, "日志保留天数必须为1到365之间的整数"
    return retention_days, None


# 布尔值字符串统一解析：代理开关等场景使用
_TRUE_VALUES = {"true", "1", "yes", "on"}


def _is_truthy(raw_value: str | None) -> bool:
    """把字符串形式的布尔值（'true'/'false'/'1'/'0' 等）统一解析为 bool。"""
    return str(raw_value or "").strip().lower() in _TRUE_VALUES


async def _validate_proxy_setting(
    key: str,
    new_value: str,
    service: SystemSettingService,
) -> str | None:
    """
    代理设置跨键校验：防止绕过前端直接 PUT 产生非法状态。

    - PUT proxy.enabled=true：要求数据库中 proxy.api_url 已非空
    - PUT proxy.api_url=''：要求当前 proxy.enabled 为 false，避免"开着代理但 URL 被清空"

    返回 None 表示校验通过，返回字符串表示错误信息。
    """
    if key not in ("proxy.enabled", "proxy.api_url"):
        return None

    # 读当前已保存的代理设置（仅用于读取另一个键的当前值）
    current_settings = await service.list_settings()

    if key == "proxy.enabled" and _is_truthy(new_value):
        current_api_url = str(current_settings.get("proxy.api_url") or "").strip()
        if not current_api_url:
            return "开启代理前请先填写代理 API 的 URL"

    if key == "proxy.api_url" and not str(new_value or "").strip():
        if _is_truthy(current_settings.get("proxy.enabled")):
            return "代理已启用，请先关闭代理再清空代理 API 的 URL"

    return None


async def _notify_log_retention_service(
    service_name: str,
    service_url: str,
    retention_days: int,
) -> dict:
    """通过内部 HTTP 接口通知目标服务刷新日志保留天数。"""
    if not service_url:
        return {
            "success": False,
            "message": f"{service_name}服务地址未配置，将由自动同步任务补齐",
        }

    try:
        response = await get_http_client().post(
            f"{service_url.rstrip('/')}/internal/logs/retention",
            json={"retention_days": retention_days},
        )
        success = bool(response.get("success"))
        default_msg = (
            f"{service_name}服务刷新成功" if success else f"{service_name}服务刷新失败"
        )
        return {
            "success": success,
            "message": str(response.get("message") or default_msg),
        }
    except Exception as e:
        logger.error(f"通知{service_name}服务刷新日志保留天数失败: {e}")
        return {
            "success": False,
            "message": f"{service_name}服务刷新失败: {str(e)}，将由自动同步任务补齐",
        }


async def _refresh_log_retention_runtime(retention_days: int) -> dict:
    """刷新当前服务并广播通知其它服务刷新日志保留天数。"""
    settings = get_settings()
    local_updated = update_log_retention(retention_days)

    results = {
        "backend_web": {
            "success": True,
            "message": "backend-web服务已刷新"
            if local_updated
            else "backend-web服务无需变更",
        },
        "websocket": await _notify_log_retention_service(
            "WebSocket",
            settings.websocket_service_url,
            retention_days,
        ),
        "scheduler": await _notify_log_retention_service(
            "Scheduler",
            settings.scheduler_service_url,
            retention_days,
        ),
        "promotion_backend": {
            "success": True,
            "message": "返佣服务将通过自动同步任务应用最新日志保留天数",
        },
    }
    return results


@router.get("/public")
async def get_public_settings(
    service: SystemSettingService = Depends(deps.get_system_setting_service),
) -> dict[str, str]:
    """获取公开的系统设置（无需登录）"""
    all_settings = await service.list_settings()
    # 只返回公开的配置项
    public_keys = {
        "registration_enabled",
        "show_default_login_info",
        "login_captcha_enabled",
        "login.system_name",
        "login.system_title",
        "login.system_description",
        "auth.footer_ad_html",
        "theme.effect",
        "theme.color_preset",
        "theme.font_family",
        "runtime.is_exe_mode",
    }
    all_settings["runtime.is_exe_mode"] = "true" if is_frozen() else "false"
    return {k: v for k, v in all_settings.items() if k in public_keys}


@router.get("")
async def get_system_settings(
    current_user: User = Depends(deps.get_current_active_user),
    service: SystemSettingService = Depends(deps.get_system_setting_service),
) -> dict[str, str]:
    settings = await service.list_settings()
    settings["runtime.is_exe_mode"] = "true" if is_frozen() else "false"
    if current_user.role == UserRole.ADMIN:
        return settings
    return {
        key: value for key, value in settings.items() if key in NON_ADMIN_ALLOWED_KEYS
    }


@router.put("/{key}", response_model=ApiResponse)
async def update_system_setting(
    key: str,
    payload: SystemSettingUpdate,
    current_user: User = Depends(deps.get_current_admin_user),
    service: SystemSettingService = Depends(deps.get_system_setting_service),
) -> ApiResponse:
    if key == "admin_password_hash":
        return ApiResponse(success=False, message="该设置需要使用专用接口修改")

    # 支付宝总开关等敏感键仅允许管理员切换；路由本身已由 get_current_admin_user
    # 保护，此处再显式拒绝以防未来误开放普通用户写入入口。
    if key in _ADMIN_ONLY_KEYS and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="仅管理员可修改该设置")
    # alipay.enabled 仅管理员可切换（默认 false）；普通用户无法到达此处。
    # 不写入真实支付宝密钥，仅切换总开关。

    # 兑换码商城地址写入校验：协议必须 https 且 host 严格等于 pay.ldxp.cn
    if key == "redemption_store_url":
        store_error = _validate_redemption_store_url(payload.value)
        if store_error:
            raise HTTPException(status_code=400, detail=store_error)

    retention_days: int | None = None
    if key == LOG_RETENTION_KEY:
        retention_days, error_message = _parse_log_retention_days(payload.value)
        if error_message:
            return ApiResponse(success=False, message=error_message)

    # 代理设置跨键校验（开启代理必须已配置 URL；代理启用中不允许清空 URL）
    proxy_error = await _validate_proxy_setting(key, payload.value, service)
    if proxy_error:
        return ApiResponse(success=False, message=proxy_error)

    await service.set_setting(key, payload.value, payload.description)

    if retention_days is None:
        return ApiResponse(success=True, message="系统设置已更新")

    refresh_results = await _refresh_log_retention_runtime(retention_days)
    failed_services = [
        service_name
        for service_name, result in refresh_results.items()
        if not bool(result.get("success"))
    ]
    if failed_services:
        return ApiResponse(
            success=True,
            message="系统设置已更新，日志保留天数已生效，部分服务将由自动同步任务补齐",
            data={
                "retention_days": retention_days,
                "refresh_results": refresh_results,
                "pending_sync_services": failed_services,
            },
        )

    return ApiResponse(
        success=True,
        message="系统设置已更新，日志保留天数已实时生效",
        data={
            "retention_days": retention_days,
            "refresh_results": refresh_results,
            "pending_sync_services": [],
        },
    )


@router.post("/test-email", response_model=ApiResponse)
async def test_email_send(
    email: str,
    current_user: User = Depends(deps.get_current_admin_user),
) -> ApiResponse:
    """发送测试邮件"""
    from app.services.email_service import send_test_email

    success, message = await send_test_email(email)
    return ApiResponse(success=success, message=message)
