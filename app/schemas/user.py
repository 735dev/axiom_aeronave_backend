from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)

from app.config.access import OverrideEffect
from app.config.auth import AVATAR_MAX_CHARS, FULL_NAME_MAX_LENGTH
from app.schemas.common import OptionalText, blank_to_none, RequestModel

_AVATAR_PATTERN = re.compile(r"^(data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+|https://\S+)$")


def _check_avatar(value: str | None) -> str | None:
    if value is not None and not _AVATAR_PATTERN.match(value):
        raise ValueError("La foto debe ser una imagen PNG, JPEG o WEBP, o una URL https.")
    return value


AvatarUrl = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_check_avatar)]


class UserRead(BaseModel):
    id: UUID
    full_name: str
    email: str
    phone: str | None = None
    avatar_url: str | None = None
    role_id: UUID
    role_code: str
    role_name: str
    aircraft_id: str | None = None
    location: str | None = None
    is_active: bool
    must_change_password: bool = False
    has_signature: bool = False
    granted_permissions: list[str] = []
    revoked_permissions: list[str] = []
    last_login_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _flatten_overrides(cls, obj: Any) -> Any:
        if isinstance(obj, dict) or not hasattr(obj, "permission_overrides"):
            return obj
        data = {name: getattr(obj, name) for name in cls.model_fields if hasattr(obj, name)}
        data["granted_permissions"] = sorted(
            o.permission.code
            for o in obj.permission_overrides
            if o.effect == OverrideEffect.GRANT.value
        )
        data["revoked_permissions"] = sorted(
            o.permission.code
            for o in obj.permission_overrides
            if o.effect == OverrideEffect.REVOKE.value
        )
        return data


class UserCreate(RequestModel):
    """Client-settable fields only. Activity, password state and signature are server-side."""

    full_name: str = Field(min_length=1, max_length=FULL_NAME_MAX_LENGTH)
    email: EmailStr
    phone: OptionalText = Field(default=None, max_length=40)
    role_id: UUID
    aircraft_id: OptionalText = Field(default=None, max_length=60)
    location: OptionalText = Field(default=None, max_length=60)
    avatar_url: AvatarUrl = Field(default=None, max_length=AVATAR_MAX_CHARS)
    granted_permissions: list[str] = Field(default_factory=list, max_length=500)
    revoked_permissions: list[str] = Field(default_factory=list, max_length=500)


class UserUpdate(RequestModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=FULL_NAME_MAX_LENGTH)
    phone: OptionalText = Field(default=None, max_length=40)
    role_id: UUID | None = None
    aircraft_id: OptionalText = Field(default=None, max_length=60)
    location: OptionalText = Field(default=None, max_length=60)
    avatar_url: AvatarUrl = Field(default=None, max_length=AVATAR_MAX_CHARS)
    is_active: bool | None = None
    granted_permissions: list[str] | None = Field(default=None, max_length=500)
    revoked_permissions: list[str] | None = Field(default=None, max_length=500)
    # Accepted only to refuse it with a clear message: the signature belongs to its owner.
    signature_url: OptionalText = Field(default=None, max_length=600_000)


class EffectivePermissionRead(BaseModel):
    code: str
    name: str
    origin: str

    model_config = ConfigDict(from_attributes=True)


class TemporaryPasswordRead(BaseModel):
    temporary_password: str
