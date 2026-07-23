"""
兑换码摘要密钥服务（纯数据库托管，失败关闭）

设计：兑换码 HMAC 摘要密钥完全由数据库 ``xy_system_settings`` 表
（key=redemption.hmac_secret）托管，不依赖 .env / 环境变量：
- 启动时若数据库已存在有效密钥 → 直接采用（保证重启一致，历史兑换码摘要仍可校验）；
- 数据库无密钥 → 使用 ``secrets`` 生成 64 位密码学安全随机密钥，**必须持久化成功后才采用**；
- 采用的密钥写回进程内模块级缓存，供兑换码生成/核销时计算 HMAC 复用。

失败关闭（务必遵守）：
- 数据库读取/写入/解密失败时，**绝不**回退进程内临时随机密钥作为运行降级方案；
  否则进程重启后临时密钥丢失，历史兑换码将全部无法验证。
- 直接抛出安全错误，由上层决定是否终止；密钥不出现在日志、异常文本或接口响应中。
- 多进程/并发首次初始化发生唯一键冲突时：回滚当前失败事务，重新读取获胜进程
  持久化的密钥并采用，保证所有实例使用同一密钥。
"""

from __future__ import annotations

import secrets

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


class RedemptionSecretUnavailableError(RuntimeError):
    """兑换码 HMAC 密钥不可用（数据库读取/写入失败或并发初始化后仍读不到）。

    异常文本不含密钥本身。失败关闭：调用方不得回退临时密钥继续运行。
    """


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


async def _insert_secret(session: AsyncSession, secret: str) -> None:
    """插入新密钥行；若另一进程已抢先插入则抛 ``IntegrityError``（由调用方处理）。

    仅在确认数据库无密钥时调用，因此使用 INSERT 而非 upsert，以让唯一键冲突
    可被捕获并回退到“读取获胜进程密钥”的路径。
    """
    record = SystemSetting(
        key=REDEMPTION_HMAC_SETTING_KEY,
        value=secret,
        description=_REDEMPTION_SECRET_SETTING_DESC,
    )
    session.add(record)
    await session.flush()


async def ensure_redemption_secret() -> str:
    """确保运行期存在一个稳定可用的兑换码 HMAC 密钥（纯数据库托管，失败关闭）。

    逻辑：
    1. 数据库已有有效密钥 → 注入进程内缓存（common 侧）并采用。
    2. 数据库无密钥 → 生成 64 位密码学安全随机密钥，**持久化成功后**才注入缓存采用。
    3. 并发首次初始化唯一键冲突 → 回滚当前事务，读取获胜进程持久化的密钥并采用。

    任何数据库读取/写入失败都抛 ``RedemptionSecretUnavailableError``（不含密钥），
    不回退临时随机密钥。进程内缓存由 ``common.services.redemption_service`` 统一持有，
    避免热路径反复查库；密钥不得输出到日志、异常或报告。
    """
    async with async_session_maker() as session:
        try:
            persisted = await _load_persisted_secret(session)
        except Exception:
            # 数据库读取失败：失败关闭，不回退临时随机密钥
            raise RedemptionSecretUnavailableError(
                "Redemption HMAC secret is unavailable; database read failed"
            )
        if persisted:
            set_redemption_secret(persisted)
            logger.info("兑换码 HMAC 密钥已从数据库加载（统一托管，重启保持一致）")
            return persisted

        new_secret = secrets.token_hex(_REDEMPTION_SECRET_LENGTH // 2)
        try:
            await _insert_secret(session, new_secret)
            await session.commit()
        except IntegrityError:
            # 另一进程已抢先插入密钥：回滚当前事务，读取获胜进程持久化的密钥
            await session.rollback()
            try:
                persisted = await _load_persisted_secret(session)
            except Exception:
                raise RedemptionSecretUnavailableError(
                    "Redemption HMAC secret conflict remains unresolved"
                )
            if not persisted:
                # 冲突后仍读不到密钥：失败关闭，不回退临时密钥
                raise RedemptionSecretUnavailableError(
                    "Redemption HMAC secret conflict remains unresolved"
                )
            set_redemption_secret(persisted)
            logger.info(
                "兑换码 HMAC 密钥并发初始化冲突已解决，采用获胜进程持久化的密钥"
            )
            return persisted
        except Exception:
            # 数据库写入失败：失败关闭，不回退临时密钥
            await session.rollback()
            raise RedemptionSecretUnavailableError(
                "Redemption HMAC secret initialization failed; database write error"
            )

        set_redemption_secret(new_secret)
        logger.warning(
            "数据库中未找到兑换码 HMAC 密钥，已自动生成密码学安全随机密钥并持久化"
        )
        return new_secret
