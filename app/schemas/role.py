from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import OptionalText, RequestModel


class RoleRead(BaseModel):
    id: UUID
    code: str
    name: str
    description: str | None = None
    is_system: bool
    is_active: bool
    permissions: list[str] = []
    users_count: int = 0

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _flatten_permissions(cls, obj: Any) -> Any:
        if isinstance(obj, dict) or not hasattr(obj, "permission_codes"):
            return obj
        data = {name: getattr(obj, name) for name in cls.model_fields if hasattr(obj, name)}
        data["permissions"] = obj.permission_codes
        return data


class RoleCreate(RequestModel):
    name: str = Field(min_length=3, max_length=120)
    description: OptionalText = Field(default=None, max_length=2000)
    permissions: list[str] = Field(default_factory=list, max_length=500)


class RoleUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    description: OptionalText = Field(default=None, max_length=2000)
    is_active: bool | None = None
    permissions: list[str] | None = Field(default=None, max_length=500)
