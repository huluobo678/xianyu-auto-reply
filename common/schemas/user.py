from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic_core import PydanticCustomError

from common.models.user import UserRole, UserStatus
from common.schemas.common import TimestampSchema
from common.utils.security import is_strong_password


class UserBase(BaseModel):
    username: str = Field(max_length=64)
    email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=32)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)
    verification_code: Optional[str] = Field(default=None, max_length=6, description="邮箱验证码")

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not is_strong_password(value):
            raise PydanticCustomError(
                "password_strength",
                "密码至少 8 位，且必须包含大写字母、小写字母、数字和特殊字符",
            )
        return value

class PhoneRegister(BaseModel):
    """Phone registration payload."""
    phone: str = Field(pattern=r"^1\d{10}$", description="11-digit Chinese mainland phone number")
    password: str = Field(min_length=8, max_length=128, description="At least 8 characters with upper/lowercase, number, and special character")
    geetest_challenge: str = Field(min_length=1, max_length=256, description="Verified Geetest challenge")

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not is_strong_password(value):
            raise PydanticCustomError(
                "password_strength",
                "密码至少 8 位，且必须包含大写字母、小写字母、数字和特殊字符",
            )
        return value


class AdminUserCreate(UserBase):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: UserRole = UserRole.MEMBER
    status: UserStatus = UserStatus.ACTIVE
    account_limit: Optional[int] = Field(default=None, ge=1)
    # 到期日（精确到秒，北京时间 naive）。不传 / null 表示永不过期。
    expire_at: Optional[datetime] = None


class UserUpdate(BaseModel):
    # 用户自助更新模型：禁止包含 role / status 等提权敏感字段。
    # 角色、状态等只能由管理员通过 AdminUserUpdate 修改。
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=32)


class AdminUserUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=1, max_length=64)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=32)
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    status: Optional[UserStatus] = None
    role: Optional[UserRole] = None
    account_limit: Optional[int] = Field(default=None, ge=1)
    # 到期日（精确到秒，北京时间 naive）。显式传 null 表示清空到期日（永不过期）。
    # 不传该字段则不修改原值（依赖 model_dump(exclude_unset=True)）。
    expire_at: Optional[datetime] = None


class UserPublic(TimestampSchema, UserBase):
    id: int
    role: UserRole
    status: UserStatus
    account_limit: Optional[int] = None
    external_id: Optional[str] = None
    last_login_at: Optional[datetime] = None
    expire_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
