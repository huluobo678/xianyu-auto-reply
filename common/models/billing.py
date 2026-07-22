from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class RecordMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class BillingPlan(RecordMixin, Base):
    __tablename__ = "xy_billing_plans"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    account_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_ai_quota: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    ai_unlimited: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    feature_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    is_free: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class BillingPlanPrice(RecordMixin, Base):
    __tablename__ = "xy_billing_plan_prices"
    __table_args__ = (
        UniqueConstraint("plan_id", "billing_cycle", name="uk_billing_plan_cycle"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    billing_cycle: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_months: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="CNY"
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="1")


class AIQuotaPackage(RecordMixin, Base):
    __tablename__ = "xy_ai_quota_packages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    base_quota: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bonus_quota: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    ai_unlimited: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    validity_days: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class BillingOrder(RecordMixin, Base):
    __tablename__ = "xy_billing_orders"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uk_billing_order_request"),
        Index("idx_billing_order_user_created", "user_id", "created_at"),
        Index("idx_billing_order_status_created", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    product_type: Mapped[str] = mapped_column(String(24), nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False)
    product_name: Mapped[str] = mapped_column(String(128), nullable=False)
    product_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="CNY"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending"
    )
    payment_channel: Mapped[str | None] = mapped_column(String(24))
    payment_qr_code: Mapped[str | None] = mapped_column(String(1024))
    payment_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    channel_trade_no: Mapped[str | None] = mapped_column(String(128), unique=True)
    entitlement_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending"
    )
    entitlement_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    entitlement_error: Mapped[str | None] = mapped_column(String(500))
    notify_received_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime)


class UserSubscription(RecordMixin, Base):
    __tablename__ = "xy_user_subscriptions"
    __table_args__ = (Index("idx_subscription_status_expire", "status", "expires_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    billing_cycle: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="active"
    )
    account_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_ai_quota: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    ai_unlimited: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    feature_snapshot: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    current_period_start: Mapped[date | None] = mapped_column(Date)
    current_period_end: Mapped[date | None] = mapped_column(Date)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    pending_plan_id: Mapped[int | None] = mapped_column(BigInteger)
    pending_plan_code: Mapped[str | None] = mapped_column(String(32))
    pending_billing_cycle: Mapped[str | None] = mapped_column(String(16))
    pending_duration_months: Mapped[int | None] = mapped_column(Integer)
    pending_account_limit: Mapped[int | None] = mapped_column(Integer)
    pending_monthly_ai_quota: Mapped[int | None] = mapped_column(BigInteger)
    pending_ai_unlimited: Mapped[bool] = mapped_column(
        nullable=False, server_default="0"
    )
    pending_feature_snapshot: Mapped[list[str] | None] = mapped_column(JSON)
    pending_source: Mapped[str | None] = mapped_column(String(24))
    source_order_id: Mapped[int | None] = mapped_column(BigInteger)


class AIQuotaGrant(RecordMixin, Base):
    __tablename__ = "xy_ai_quota_grants"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_ai_quota_grant_idempotency"),
        Index("idx_ai_grant_user_expire", "user_id", "expires_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    grant_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False)
    total_quota: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining_quota: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ai_unlimited: Mapped[bool] = mapped_column(nullable=False, server_default="0")
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )


class EntitlementLedger(Base):
    __tablename__ = "xy_entitlement_ledger"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_entitlement_ledger_idempotency"),
        Index("idx_entitlement_user_created", "user_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entitlement_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    grant_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
