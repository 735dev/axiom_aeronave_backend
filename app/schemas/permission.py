from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PermissionRead(BaseModel):
    id: UUID
    code: str
    name: str
    description: str | None = None
    module_code: str
    is_system: bool
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
