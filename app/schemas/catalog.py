from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config.catalogs import CATALOG_ITEM_CODE_MAX
from app.schemas.common import OptionalText, RequestModel


class CatalogDefinitionRead(BaseModel):
    key: str
    name: str
    description: str
    items_count: int


class UserOptionRead(BaseModel):
    """A person offered in a form dropdown. Only the name leaves the server."""

    value: str
    label: str


class CatalogItemRead(BaseModel):
    id: UUID
    catalog: str
    code: str
    name: str
    extra: str | None = None
    is_system: bool
    is_active: bool
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class CatalogItemCreate(RequestModel):
    catalog: str = Field(max_length=40)
    name: str = Field(min_length=2, max_length=200)
    code: OptionalText = Field(default=None, max_length=CATALOG_ITEM_CODE_MAX)
    extra: OptionalText = Field(default=None, max_length=2000)


class CatalogItemUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    extra: OptionalText = Field(default=None, max_length=2000)
    is_active: bool | None = None
