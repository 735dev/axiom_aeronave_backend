from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config.auth import AVATAR_MAX_CHARS, FULL_NAME_MAX_LENGTH
from app.schemas.common import OptionalText, RequestModel
from app.schemas.user import AvatarUrl


class LoginRequest(RequestModel):
    # Plain strings on purpose: a malformed email answers 401 like any wrong credential.
    email: str = Field(max_length=255)
    password: str = Field(max_length=256)


class RefreshRequest(RequestModel):
    refresh_token: str = Field(max_length=4096)


class PasswordChangeRequest(RequestModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class ProfileUpdate(RequestModel):
    """What a user may change about themselves. Anything else is rejected, not ignored,
    so an attempt to send `role_id` or `is_active` is visible instead of silent."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=FULL_NAME_MAX_LENGTH)
    phone: OptionalText = Field(default=None, max_length=40)
    avatar_url: AvatarUrl = Field(default=None, max_length=AVATAR_MAX_CHARS)


class SessionUser(BaseModel):
    id: UUID
    email: str
    full_name: str
    avatar_url: str | None = None
    phone: str | None = None
    role_id: UUID
    role_code: str
    role_name: str
    aircraft_id: str | None = None
    location: str | None = None
    has_signature: bool = False
    must_change_password: bool = False

    model_config = ConfigDict(from_attributes=True)


class SessionResponse(BaseModel):
    user: SessionUser
    permissions: list[str]

    model_config = ConfigDict(from_attributes=True)


class LoginResponse(SessionResponse):
    token: str
    refresh_token: str


class TokenPairResponse(BaseModel):
    token: str
    refresh_token: str

    model_config = ConfigDict(from_attributes=True)


class SignatureRead(BaseModel):
    signature_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SignatureUpdate(RequestModel):
    """`signature_url` is a data URL (PNG or JPEG). Empty or null removes the signature."""

    signature_url: str | None = Field(default=None, max_length=600_000)


class MessageResponse(BaseModel):
    detail: str
