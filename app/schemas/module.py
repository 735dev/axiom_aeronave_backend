from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import OptionalText, RequestModel


class ModuleRead(BaseModel):
    id: UUID
    code: str
    name: str
    description: str | None = None
    route: str | None = None
    icon: str | None = None
    menu_permission: str | None = None
    show_in_menu: bool
    sort_order: int
    is_system: bool
    is_active: bool
    permissions_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class ModuleUpdate(RequestModel):
    """Menu configuration only. There is no create and no delete."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: OptionalText = Field(default=None, max_length=2000)
    route: OptionalText = Field(default=None, max_length=120)
    icon: OptionalText = Field(default=None, max_length=40)
    menu_permission: OptionalText = Field(default=None, max_length=120)
    show_in_menu: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None
