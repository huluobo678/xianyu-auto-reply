"""支付宝入口守卫。

第一版收费改为兑换码 + 链动小铺，支付宝三个入口（套餐支付 / 余额充值 / 广告付款
及其回调）默认由 ``alipay.enabled``（默认 false）总开关保护：

- ``alipay.enabled=false`` 时，受守卫入口不调用支付宝 SDK、不创建真实支付订单、
  不修改支付状态，直接抛 ``AlipayDisabledError``，由路由转换为 503 / 未开通提示；
- 不删除支付宝服务与配置代码，将来管理员在系统设置中开启总开关即可重启；
- 不写入真实支付宝密钥。

守卫只读取 ``alipay.enabled`` 设置项，不接触私钥/公钥等敏感配置。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.system_setting import SystemSetting

# 支付宝总开关设置项 key，与 system_setting_service.DEFAULT_SYSTEM_SETTINGS 对齐
ALIPAY_ENABLED_KEY = "alipay.enabled"

_TRUE_VALUES = {"true", "1", "yes", "on"}


class AlipayDisabledError(RuntimeError):
    """支付宝入口被 alipay.enabled=false 关闭时抛出，路由转换为 503 / 未开通。"""


def _is_truthy(raw_value: str | None) -> bool:
    """把字符串形式的布尔值统一解析为 bool。"""
    return str(raw_value or "").strip().lower() in _TRUE_VALUES


async def is_alipay_enabled(session: AsyncSession) -> bool:
    """读取 alipay.enabled 总开关。默认缺失或非 true 均视为关闭。"""
    value = await session.scalar(
        select(SystemSetting.value).where(SystemSetting.key == ALIPAY_ENABLED_KEY)
    )
    return _is_truthy(value)


async def require_alipay_enabled(session: AsyncSession) -> None:
    """受守卫入口调用：alipay.enabled=false 时抛 AlipayDisabledError。"""
    if not await is_alipay_enabled(session):
        raise AlipayDisabledError("支付未开通，请使用兑换码购买")
