from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class AIQuotaConfig(Base):
    __tablename__ = "xy_ai_quota_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    package_quota: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    independent_quota: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AIAccountQuotaConfig(Base):
    __tablename__ = "xy_ai_account_quota_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_pk: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    monthly_quota: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    requests_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AIUserMonthlyUsage(Base):
    __tablename__ = "xy_ai_user_monthly_usage"
    __table_args__ = (UniqueConstraint("user_id", "period_start", name="uk_ai_user_usage_period"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    effective_replies: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    reserved_replies: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    estimated_cost: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False, default=0, server_default="0")
    warned_80_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    warned_100_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AIAccountMonthlyUsage(Base):
    __tablename__ = "xy_ai_account_monthly_usage"
    __table_args__ = (UniqueConstraint("account_pk", "period_start", name="uk_ai_account_usage_period"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_pk: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    effective_replies: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    reserved_replies: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    estimated_cost: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class AIUsageRequest(Base):
    __tablename__ = "xy_ai_usage_requests"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_ai_usage_idempotency"),
        Index("idx_ai_usage_user_period", "user_id", "period_start"),
        Index("idx_ai_usage_account_period", "account_pk", "period_start"),
        Index("idx_ai_usage_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_pk: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved", server_default="reserved")
    release_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    token_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False, default=0, server_default="0")
    auto_reply_log_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
