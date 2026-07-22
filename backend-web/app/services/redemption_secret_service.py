"""
兑换码摘要密钥服务（纯数据库托管）

设计：兑换码 HMAC 摘要密钥完全由数据库 ``xy_system_settings`` 表
（key=redemption.hmac_secret）托管，不依赖 .env / 环境变量：
- 启动时若数据库已存在有效密钥 → 直接采用（保证重启一致，历史兑换码摘要仍可校验）；
- 数据库无密钥 → 使用 ``secrets`` 生成 64 位密码学安全随机密钥并写入数据库，再采用；
- 采用的密钥写回进程内模块级缓存，供兑换码生成/核销时计算 HMAC 复用。

安全要求：
- 密钥不得输出到日志、异常或报告；
- 仿照 ``ensure_jwt_secret_key`` 的并发与持久化模式（读-判-写）。
"""

from __future__ import annotations

import secrets

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.db.session import async_session_maker
from common.models.system_setting import SystemSetting
from common.services.redemption_service import (
    REDEMPTION_HMAC_SETTING_KEY,
    set_redemption_secret,
)

# 设置项 key 与描述（key 单一权威来源在 common.services.redemption_service）
_REDEMPTION_SECRET_SETTING_DESC = (
    "兑换码 HMAC 摘要密钥（由数据库统一托管，自动生成，请勿泄露）"
)

# 密钥长度（64 位十六进制 = 32 字节熵），与 generate_jwt_secret 的随机密钥模式一致
_REDEMPTION_SECRET_LENGTH = 64


def _is_valid_secret(value: str | None) -> bool:
    """判断密钥是否可用：非空且长度达到要求。"""
    return bool(value) and len(value) >= _REDEMPTION_SECRET_LENGTH


async def _load_persisted_secret(session: AsyncSession) -> str | None:
    """从数据库读取已持久化的密钥；不存在或不可用时返回 None。"""
    result = await session.execute(
        select(SystemSetting.value).where(
            SystemSetting.key == REDEMPTION_HMAC_SETTING_KEY
        )
    )
    row = result.scalar_one_or_none()
    if _is_valid_secret(row):
        return row
    return None


async def _persist_secret(session: AsyncSession, secret: str) -> None:
    """将密钥写入数据库（存在则更新，不存在则插入）。"""
    result = await session.execute(
        select(SystemSetting).where(SystemSetting.key == REDEMPTION_HMAC_SETTING_KEY)
    )
    record = result.scalar_one_or_none()
    if record:
        record.value = secret
    else:
        record = SystemSetting(
            key=REDEMPTION_HMAC_SETTING_KEY,
            value=secret,
            description=_REDEMPTION_SECRET_SETTING_DESC,
        )
    session.add(record)
    await session.commit()


async def ensure_redemption_secret() -> str:
    """确保运行期存在一个稳定可用的兑换码 HMAC 密钥（纯数据库托管）。

    逻辑：
    1. 数据库已有有效密钥 → 注入进程内缓存（common 侧）并采用。
    2. 数据库无密钥 → 生成 64 位密码学安全随机密钥并写入数据库，再注入缓存采用。

    进程内缓存由 ``common.services.redemption_service`` 统一持有，避免热路径反复查库；
    密钥不得输出到日志、异常或报告。
    """
    try:
        async with async_session_maker() as session:
            persisted = await _load_persisted_secret(session)
            if persisted:
                set_redemption_secret(persisted)
                logger.info("兑换码 HMAC 密钥已从数据库加载（统一托管，重启保持一致）")
                return persisted

            new_secret = secrets.token_hex(_REDEMPTION_SECRET_LENGTH // 2)
            await _persist_secret(session, new_secret)
            set_redemption_secret(new_secret)
            logger.warning(
                "数据库中未找到兑换码 HMAC 密钥，已自动生成密码学安全随机密钥并持久化"
            )
            return new_secret
    except Exception as e:
        # 数据库不可用等异常不应阻断启动；退回进程内临时密钥并告警
        fallback = secrets.token_hex(_REDEMPTION_SECRET_LENGTH // 2)
        set_redemption_secret(fallback)
        logger.opt(exception=e).error(
            "初始化兑换码 HMAC 密钥失败（数据库不可用？），本次启动使用进程内临时密钥；"
            "恢复数据库后重启即可统一托管"
        )
        return fallback
