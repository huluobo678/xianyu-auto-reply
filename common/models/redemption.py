from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class RedemptionBatch(Base):
    """兑换码批次。

    product_type 取值：plan / ai_quota_package。
    - plan：billing_cycle=monthly/quarterly，duration_months 表示月数。
    - ai_quota_package：validity_days 表示有效期天数（无限包 30 天）。
    """

    __tablename__ = "xy_redemption_batches"
    __table_args__ = (
        Index("idx_rb_product_code", "product_type", "product_code"),
        Index("idx_rb_disabled_created", "disabled", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    product_type: Mapped[str] = mapped_column(String(24), nullable=False)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False)
    product_name: Mapped[str] = mapped_column(String(128), nullable=False)
    billing_cycle: Mapped[str | None] = mapped_column(String(16))
    duration_months: Mapped[int | None] = mapped_column(Integer)
    validity_days: Mapped[int | None] = mapped_column(Integer)
    ai_unlimited: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    # 生成批次时冻结的完整、不可变权益快照。兑换时只依赖此快照发放权益，
    # 不再重新读取当前套餐/加量包目录，避免目录后续变更影响已售出的兑换码。
    entitlement_snapshot: Mapped[dict | None] = mapped_column(JSON)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 一次性导出加密载荷（Fernet/AES-128-CBC+HMAC，密钥由 redemption.hmac_secret
    # 经 HKDF 派生，失败关闭）。创建批次时写入加密后的完整兑换码；导出成功后
    # 清空（NULL）。兑换码主记录只保存摘要与尾4位，明文永不以明文长期落库。
    # exported_at 为空且 export_payload 非空 → 待导出；exported_at 非空 → 已导出。
    export_payload: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    disabled: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    disable_reason: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RedemptionCode(Base):
    """单条兑换码。只保存 HMAC-SHA256 摘要与尾 4 位，不保存完整码。

    code_digest 使用服务端密钥的 HMAC-SHA256（64 位 hex），用于按完整码反查。
    status：unused / used / disabled / expired。
    """

    __tablename__ = "xy_redemption_codes"
    __table_args__ = (
        UniqueConstraint("code_digest", name="uk_redemption_code_digest"),
        Index("idx_rc_batch_status", "batch_id", "status"),
        Index("idx_rc_used_by", "used_by"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unused"
    )
    used_by: Mapped[int | None] = mapped_column(BigInteger)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    redemption_record_id: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    disabled: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    disable_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RedemptionRecord(Base):
    """兑换审计记录。一次成功兑换写一条，幂等键唯一防止重复发放。"""

    __tablename__ = "xy_redemption_records"
    __table_args__ = (
        # 幂等键按用户隔离：同一用户同一幂等键只能对应一次兑换；
        # 不同用户可复用相同幂等键，互不影响、互不可读。
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uk_redemption_record_idempotency",
        ),
        Index("idx_rr_user_created", "user_id", "created_at"),
        Index("idx_rr_batch_code", "batch_id", "code_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_type: Mapped[str] = mapped_column(String(24), nullable=False)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False)
    grant_id: Mapped[int | None] = mapped_column(BigInteger)
    ledger_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
