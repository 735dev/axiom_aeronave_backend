from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.config.tickets import (
    AUTH_NOTE_MAX,
    COMMENT_MAX,
    MAX_RELATIONS,
    OBSERVATION_MAX,
    TITLE_MAX,
    Confidence,
    DecisionValue,
    RelationKind,
)
from app.schemas.attachment import AttachmentRead
from app.schemas.closing import ClosingStampRead
from app.schemas.common import OptionalText, RequestModel
from app.schemas.reading import ExtractedFieldRead, ExtractionRead  # noqa: F401
from app.utils.ticket_payload import hide_restricted


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class RelationBody(RequestModel):
    kind: RelationKind
    code: str = Field(min_length=1, max_length=60)
    label: OptionalText = Field(default=None, max_length=200)


class TicketCreate(RequestModel):
    """What a client may set. Status, code, money, closing and rule results are the
    server's: they are not in this schema, so they cannot be forged."""

    type_code: str = Field(min_length=1, max_length=60)
    title: OptionalText = Field(default=None, max_length=TITLE_MAX)
    parent_id: UUID | None = None
    assigned_role_id: UUID | None = None
    assigned_user_id: UUID | None = None
    due_at: datetime | None = None
    relations: list[RelationBody] = Field(default_factory=list, max_length=MAX_RELATIONS)
    payload: dict[str, Any] = Field(default_factory=dict)

    _due = field_validator("due_at")(_aware)


class TicketUpdate(RequestModel):
    status_code: str | None = Field(default=None, min_length=1, max_length=60)
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    payload: dict[str, Any] | None = None
    comment: str | None = Field(default=None, min_length=1, max_length=COMMENT_MAX)
    assigned_role_id: UUID | None = None
    assigned_user_id: UUID | None = None
    due_at: datetime | None = None
    relations: list[RelationBody] | None = Field(default=None, max_length=MAX_RELATIONS)

    _due = field_validator("due_at")(_aware)


class DecisionCreate(RequestModel):
    step_code: str = Field(min_length=1, max_length=60)
    decision: DecisionValue = DecisionValue.DONE
    observation: OptionalText = Field(default=None, max_length=OBSERVATION_MAX)


class AuthorizeBody(RequestModel):
    note: OptionalText = Field(default=None, max_length=AUTH_NOTE_MAX)


class ExtractedFieldBody(RequestModel):
    key: str = Field(min_length=1, max_length=60)
    value: str | int | float
    confidence: Confidence = Confidence.HIGH


class ExtractionCreate(RequestModel):
    fields: list[ExtractedFieldBody] = Field(min_length=1, max_length=50)


class ApprovedFieldBody(RequestModel):
    key: str = Field(min_length=1, max_length=60)
    value: str | int | float


class ExtractionValidate(RequestModel):
    fields: list[ApprovedFieldBody] = Field(default_factory=list, max_length=50)
    confirmed: list[str] = Field(default_factory=list, max_length=50)


class MoneyRead(BaseModel):
    amount: float
    currency: str
    currency_name: str | None = None
    rate: float
    amount_base: float
    base_currency: str
    rated_at: datetime
    rate_source: str


class DecisionRead(BaseModel):
    id: UUID
    step_code: str
    step_name: str
    decision: str
    observation: str | None = None
    decided_by: str
    decided_at: datetime
    signed: bool = False

    model_config = ConfigDict(from_attributes=True)


class CommentRead(BaseModel):
    id: UUID
    author: str
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RelationRead(BaseModel):
    id: str
    kind: str
    code: str
    label: str | None = None


class RuleHitRead(BaseModel):
    rule_id: str
    rule_name: str
    action: str
    message: str
    at: datetime


class AuthorizationRead(BaseModel):
    by: str
    role: str
    at: datetime
    note: str | None = None
    amount_base: float


class TicketRead(BaseModel):
    id: UUID
    code: str
    control_number: str | None = None
    type_code: str
    type_name: str
    type_is_root: bool
    title: str
    status_code: str
    status_name: str
    status_color: str
    status_is_final: bool
    status_counts_as_committed: bool
    parent_id: UUID | None = None
    parent_code: str | None = None
    assigned_role_id: UUID | None = None
    assigned_role_name: str | None = None
    assigned_user_id: UUID | None = None
    assigned_user_name: str | None = None
    created_by: str
    created_by_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    due_at: datetime | None = None
    closed_at: datetime | None = None
    accounting_date: date
    aircraft_code: str | None = None
    payload: dict[str, Any] = {}
    money: MoneyRead | None = None
    relations: list[RelationRead] = []
    decisions: list[DecisionRead] = []
    comments: list[CommentRead] = []
    rule_hits: list[RuleHitRead] = []
    authorization: AuthorizationRead | None = None
    closing: ClosingStampRead | None = None
    attachments: int = 0
    files: list[AttachmentRead] = []
    children_count: int = 0

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _from_ticket(cls, obj: Any, info: ValidationInfo) -> Any:
        if isinstance(obj, dict) or not hasattr(obj, "type"):
            return obj
        ticket_type, status = obj.type, obj.status
        # Without a context nothing restricted is shown: fail closed.
        held = (info.context or {}).get("permissions", frozenset())
        money = None
        if obj.amount is not None:
            money = {
                "amount": obj.amount,
                "currency": obj.currency,
                "currency_name": obj.currency_name,
                "rate": obj.rate,
                "amount_base": obj.amount_base,
                "base_currency": obj.base_currency,
                "rated_at": obj.rated_at,
                "rate_source": obj.rate_source,
            }
        return {
            "id": obj.id,
            "code": obj.code,
            "control_number": obj.control_number,
            "type_code": ticket_type.code,
            "type_name": ticket_type.name,
            "type_is_root": ticket_type.is_root,
            "title": obj.title,
            "status_code": status.code,
            "status_name": status.name,
            "status_color": status.color,
            "status_is_final": status.is_final,
            "status_counts_as_committed": status.counts_as_committed,
            "parent_id": obj.parent_id,
            "parent_code": obj.parent_code,
            "assigned_role_id": obj.assigned_role_id,
            "assigned_role_name": obj.assigned_role_name,
            "assigned_user_id": obj.assigned_user_id,
            "assigned_user_name": obj.assigned_user_name,
            "created_by": obj.created_by_name,
            "created_by_id": obj.created_by_id,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
            "due_at": obj.due_at,
            "closed_at": obj.closed_at,
            "accounting_date": obj.accounting_date,
            "aircraft_code": obj.aircraft_code,
            "payload": hide_restricted(obj.payload or {}, ticket_type.fields, held),
            "money": money,
            "relations": [
                {"id": f"rel-{r['kind']}-{r['code']}", **r} for r in (obj.relations or [])
            ],
            "decisions": obj.decisions,
            "comments": obj.comments,
            "rule_hits": obj.rule_hits or [],
            "authorization": obj.authorization,
            "closing": (
                ClosingStampRead.from_record(obj.closing_record)
                if getattr(obj, "closing_record", None)
                else None
            ),
            "attachments": obj.attachments_count,
            "files": [AttachmentRead.model_validate(a) for a in obj.attachments],
            "children_count": obj.children_count,
        }
