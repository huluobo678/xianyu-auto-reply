from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class ConnectorDevice(Base):
    __tablename__ = "xy_connector_devices"
    __table_args__ = (
        UniqueConstraint("device_uuid", name="uk_connector_device_uuid"),
        Index("idx_connector_device_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    device_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    device_name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="windows", server_default="windows")
    app_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_version: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ConnectorAccountBinding(Base):
    __tablename__ = "xy_connector_account_bindings"
    __table_args__ = (
        UniqueConstraint("account_id", name="uk_connector_binding_account"),
        UniqueConstraint("device_id", "account_id", name="uk_connector_device_account"),
        Index("idx_connector_binding_owner_status", "owner_id", "connection_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    connection_status: Mapped[str] = mapped_column(String(24), nullable=False, default="offline", server_default="offline")
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ConnectorCommand(Base):
    __tablename__ = "xy_connector_commands"
    __table_args__ = (
        UniqueConstraint("command_id", name="uk_connector_command_id"),
        UniqueConstraint("idempotency_key", name="uk_connector_command_idempotency"),
        Index("idx_connector_command_device_status", "device_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    command_id: Mapped[str] = mapped_column(String(64), nullable=False)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    command_type: Mapped[str] = mapped_column(String(40), nullable=False)
    safe_payload: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ConnectorEvent(Base):
    __tablename__ = "xy_connector_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uk_connector_event_id"),
        UniqueConstraint("idempotency_key", name="uk_connector_event_idempotency"),
        Index("idx_connector_event_device_created", "device_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_id: Mapped[str | None] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False)
    safe_metadata: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ConnectorReleaseVersion(Base):
    __tablename__ = "xy_connector_release_versions"
    __table_args__ = (UniqueConstraint("platform", "version", name="uk_connector_release_platform_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="windows-x64", server_default="windows-x64")
    download_url: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    minimum_supported_version: Mapped[str | None] = mapped_column(String(32))
    mandatory: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="0")
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
