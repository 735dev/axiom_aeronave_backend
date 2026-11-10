from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config.tickets import (
    MAX_CHILD_TYPES,
    MAX_FIELDS,
    MAX_OPTIONS,
    MAX_STATUSES,
    MAX_STEPS,
    FieldRole,
    FieldType,
    FieldWidth,
    RelationKind,
    StatusColor,
)
from app.schemas.common import OptionalText, RequestModel


class OptionBody(RequestModel):
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)


class FieldBody(RequestModel):
    key: OptionalText = Field(default=None, max_length=60)
    label: str = Field(min_length=1, max_length=200)
    type: FieldType
    required: bool = False
    width: FieldWidth = FieldWidth.FULL
    help: OptionalText = Field(default=None, max_length=1000)
    options: list[OptionBody] | None = Field(default=None, max_length=MAX_OPTIONS)
    catalog: OptionalText = Field(default=None, max_length=40)
    read_only: bool = False
    role: FieldRole | None = None
    depends_on: OptionalText = Field(default=None, max_length=60)
    view_permission: OptionalText = Field(default=None, max_length=120)
    edit_permission: OptionalText = Field(default=None, max_length=120)


class StatusBody(RequestModel):
    code: OptionalText = Field(default=None, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    color: StatusColor = StatusColor.DEFAULT
    is_initial: bool = False
    is_final: bool = False
    counts_as_committed: bool = True
    next_codes: list[str] | None = Field(default=None, max_length=MAX_STATUSES)


class StepBody(RequestModel):
    code: OptionalText = Field(default=None, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    role_id: UUID | None = None
    responsible_area: OptionalText = Field(default=None, max_length=120)
    required: bool = True
    requires_signature: bool = False


class TicketTypeCreate(RequestModel):
    name: str = Field(min_length=3, max_length=120)
    code: OptionalText = Field(default=None, max_length=60)
    description: OptionalText = Field(default=None, max_length=2000)
    is_root: bool = False
    child_type_codes: list[str] = Field(default_factory=list, max_length=MAX_CHILD_TYPES)
    default_role_id: UUID | None = None
    blocks_parent_close: bool = False
    relation_kinds: list[RelationKind] = Field(default_factory=list, max_length=5)
    fields: list[FieldBody] = Field(min_length=1, max_length=MAX_FIELDS)
    statuses: list[StatusBody] = Field(min_length=1, max_length=MAX_STATUSES)
    steps: list[StepBody] = Field(default_factory=list, max_length=MAX_STEPS)


class TicketTypeUpdate(RequestModel):
    """Sending `fields`, `statuses` or `steps` replaces the whole collection."""

    name: str | None = Field(default=None, min_length=3, max_length=120)
    description: OptionalText = Field(default=None, max_length=2000)
    is_root: bool | None = None
    child_type_codes: list[str] | None = Field(default=None, max_length=MAX_CHILD_TYPES)
    default_role_id: UUID | None = None
    blocks_parent_close: bool | None = None
    relation_kinds: list[RelationKind] | None = Field(default=None, max_length=5)
    is_active: bool | None = None
    fields: list[FieldBody] | None = Field(default=None, min_length=1, max_length=MAX_FIELDS)
    statuses: list[StatusBody] | None = Field(default=None, min_length=1, max_length=MAX_STATUSES)
    steps: list[StepBody] | None = Field(default=None, max_length=MAX_STEPS)


class FieldRead(BaseModel):
    id: UUID
    key: str
    label: str
    type: str
    required: bool
    width: str
    help: str | None = None
    options: list[dict[str, str]] | None = None
    catalog: str | None = None
    read_only: bool
    role: str | None = None
    depends_on: str | None = None
    view_permission: str | None = None
    edit_permission: str | None = None
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class StatusRead(BaseModel):
    id: UUID
    code: str
    name: str
    color: str
    sort_order: int
    is_initial: bool
    is_final: bool
    counts_as_committed: bool
    next_codes: list[str] | None = None

    model_config = ConfigDict(from_attributes=True)


class StepRead(BaseModel):
    id: UUID
    code: str
    name: str
    role_id: UUID | None = None
    role_name: str | None = None
    responsible_area: str | None = None
    required: bool
    requires_signature: bool = False
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class TicketTypeRead(BaseModel):
    id: UUID
    code: str
    name: str
    description: str | None = None
    is_root: bool
    child_type_codes: list[str] = []
    default_role_id: UUID | None = None
    default_role_name: str | None = None
    blocks_parent_close: bool
    relation_kinds: list[str] = []
    is_system: bool
    is_active: bool
    tickets_count: int = 0
    fields: list[FieldRead] = []
    statuses: list[StatusRead] = []
    steps: list[StepRead] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def dump_body(model: BaseModel) -> dict[str, Any]:
    """Request body as plain dicts/values (enums to their string value)."""
    return model.model_dump(mode="json", exclude_unset=False)
