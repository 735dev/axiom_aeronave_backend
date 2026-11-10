"""Request and response shapes of the bulk imports."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.config.catalogs import CATALOG_ITEM_CODE_MAX
from app.config.auth import FULL_NAME_MAX_LENGTH
from app.schemas.common import OptionalText, RequestModel
from app.utils.bulk_import import IMPORT_MAX_ROWS


class ImportRowError(BaseModel):
    row: int
    field: str | None = None
    message: str

    model_config = ConfigDict(from_attributes=True)


class ImportResult(BaseModel):
    dry_run: bool
    total: int
    created: int
    updated: int
    skipped: int
    errors: list[ImportRowError]

    model_config = ConfigDict(from_attributes=True)


class CatalogImportRow(RequestModel):
    """Lenient on purpose: a bad value is a row error, not a rejected request."""

    code: OptionalText = Field(default=None, max_length=CATALOG_ITEM_CODE_MAX * 4)
    name: OptionalText = Field(default=None, max_length=1000)
    extra: OptionalText = Field(default=None, max_length=4000)


class CatalogImportRequest(RequestModel):
    catalog: str = Field(max_length=40)
    rows: list[CatalogImportRow] = Field(max_length=IMPORT_MAX_ROWS)
    dry_run: bool = False


class UserImportRow(RequestModel):
    full_name: OptionalText = Field(default=None, max_length=FULL_NAME_MAX_LENGTH * 4)
    email: OptionalText = Field(default=None, max_length=400)
    phone: OptionalText = Field(default=None, max_length=200)
    role_code: OptionalText = Field(default=None, max_length=120)
    location: OptionalText = Field(default=None, max_length=200)
    aircraft_id: OptionalText = Field(default=None, max_length=200)


class UserImportRequest(RequestModel):
    rows: list[UserImportRow] = Field(max_length=IMPORT_MAX_ROWS)
    dry_run: bool = False
