"""Tickets: one dynamic work entity whose shape is defined by its type.

A ticket type owns its fields (the form), statuses (the life cycle) and steps (who must
decide what). The ticket keeps its content in `payload` (JSONB), validated against the
type, plus the few columns the system itself needs to query: status, parent, owner,
accounting date, aircraft and the frozen money.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, deferred, mapped_column, relationship

from app.models.access import AppUser, Role
from app.models.attachment import TicketAttachment  # noqa: F401  (relationship target)
from app.models.base import UUID_TYPE, Base, IdMixin, TimestampMixin

JSON_LIST_DEFAULT = text("'[]'::jsonb")
JSON_OBJECT_DEFAULT = text("'{}'::jsonb")


class TicketType(IdMixin, TimestampMixin, Base):
    __tablename__ = "ticket_type"

    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_root: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    child_type_codes: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=JSON_LIST_DEFAULT, nullable=False
    )
    default_role_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("role.id", ondelete="SET NULL")
    )
    blocks_parent_close: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    relation_kinds: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=JSON_LIST_DEFAULT, nullable=False
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    fields: Mapped[list["TicketTypeField"]] = relationship(
        "TicketTypeField",
        lazy="selectin",
        order_by="TicketTypeField.sort_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    statuses: Mapped[list["TicketTypeStatus"]] = relationship(
        "TicketTypeStatus",
        lazy="selectin",
        order_by="TicketTypeStatus.sort_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    steps: Mapped[list["TicketTypeStep"]] = relationship(
        "TicketTypeStep",
        lazy="selectin",
        order_by="TicketTypeStep.sort_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def initial_status(self) -> "TicketTypeStatus":
        return next((s for s in self.statuses if s.is_initial), self.statuses[0])

    def status_by_code(self, code: str) -> "TicketTypeStatus | None":
        return next((s for s in self.statuses if s.code == code), None)

    def step_by_code(self, code: str) -> "TicketTypeStep | None":
        return next((s for s in self.steps if s.code == code), None)

    def field_by_role(self, role: str) -> "TicketTypeField | None":
        return next((f for f in self.fields if f.role == role), None)

    def field_labels(self) -> dict[str, str]:
        return {f.key: f.label for f in self.fields}


class TicketTypeField(IdMixin, Base):
    __tablename__ = "ticket_type_field"

    type_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_type.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    width: Mapped[str] = mapped_column(String(10), default="full", nullable=False)
    help: Mapped[str | None] = mapped_column(Text)
    options: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
    catalog: Mapped[str | None] = mapped_column(String(40))
    read_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    role: Mapped[str | None] = mapped_column(String(30))
    view_permission: Mapped[str | None] = mapped_column(String(120))
    edit_permission: Mapped[str | None] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("type_id", "key", name="uq_ticket_type_field_type_key"),
        UniqueConstraint("type_id", "role", name="uq_ticket_type_field_type_role"),
    )


class TicketTypeStatus(IdMixin, Base):
    __tablename__ = "ticket_type_status"

    type_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_type.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="default", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_initial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    counts_as_committed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Codes this status may move to. NULL means "any other non-final-locked status".
    next_codes: Mapped[list[str] | None] = mapped_column(JSONB)

    __table_args__ = (UniqueConstraint("type_id", "code", name="uq_ticket_type_status_type_code"),)


class TicketTypeStep(IdMixin, Base):
    __tablename__ = "ticket_type_step"

    type_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_type.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    role_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("role.id", ondelete="SET NULL")
    )
    responsible_area: Mapped[str | None] = mapped_column(String(120))
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("type_id", "code", name="uq_ticket_type_step_type_code"),)


class TicketCounter(Base):
    """Last number handed out per series (TK, one per aircraft and year, control numbers)."""

    __tablename__ = "ticket_counter"

    series: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)


class Ticket(IdMixin, TimestampMixin, Base):
    __tablename__ = "ticket"

    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    control_number: Mapped[str | None] = mapped_column(String(40))
    type_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_type.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status_id: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("ticket_type_status.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("ticket.id", ondelete="RESTRICT"), index=True
    )
    assigned_role_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("role.id", ondelete="SET NULL"), index=True
    )
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_by_name: Mapped[str] = mapped_column(String(150), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=JSON_OBJECT_DEFAULT, nullable=False
    )
    relations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=JSON_LIST_DEFAULT, nullable=False
    )
    # Promoted from the payload because reports and numbering query them.
    accounting_date: Mapped[date] = mapped_column(Date, nullable=False)
    aircraft_code: Mapped[str | None] = mapped_column(String(60))

    # Money, frozen at the first resolution (see app/utils/money.py).
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(10))
    currency_name: Mapped[str | None] = mapped_column(String(120))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    amount_base: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    base_currency: Mapped[str | None] = mapped_column(String(10))
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_source: Mapped[str | None] = mapped_column(String(20))

    rule_hits: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=JSON_LIST_DEFAULT, nullable=False
    )
    authorization: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    type: Mapped[TicketType] = relationship(TicketType, lazy="selectin")
    status: Mapped[TicketTypeStatus] = relationship(TicketTypeStatus, lazy="selectin")
    decisions: Mapped[list["TicketDecision"]] = relationship(
        "TicketDecision",
        lazy="selectin",
        order_by="TicketDecision.decided_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    comments: Mapped[list["TicketComment"]] = relationship(
        "TicketComment",
        lazy="selectin",
        order_by="TicketComment.created_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    attachments: Mapped[list["TicketAttachment"]] = relationship(
        "TicketAttachment",
        primaryjoin="and_(Ticket.id == TicketAttachment.ticket_id, "
        "TicketAttachment.deleted_at.is_(None))",
        order_by="TicketAttachment.uploaded_at, TicketAttachment.id",
        lazy="selectin",
        viewonly=True,
    )

    __table_args__ = (
        Index("ix_ticket_accounting_date", "accounting_date"),
        Index("ix_ticket_aircraft_code", "aircraft_code"),
        Index("ix_ticket_payload", "payload", postgresql_using="gin"),
        Index("ix_ticket_created_at", "created_at"),
    )

    @property
    def attachments_count(self) -> int:
        """Live (not removed) attachments; the 'every expense needs a support' rule reads it."""
        return len(self.attachments)

    @property
    def money_resolved(self) -> bool:
        return self.amount is not None


class TicketDecision(IdMixin, Base):
    __tablename__ = "ticket_decision"

    ticket_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_code: Mapped[str] = mapped_column(String(60), nullable=False)
    step_name: Mapped[str] = mapped_column(String(160), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    observation: Mapped[str | None] = mapped_column(Text)
    decided_by_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    decided_by: Mapped[str] = mapped_column(String(150), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # A `done` decision copies the signer's signature at that moment (evidence is frozen).
    # The image is deferred: responses only say whether the decision is signed.
    signed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    signature_url: Mapped[str | None] = deferred(mapped_column(Text))

    __table_args__ = (UniqueConstraint("ticket_id", "step_code", name="uq_ticket_decision_step"),)


class TicketComment(IdMixin, Base):
    __tablename__ = "ticket_comment"

    ticket_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    author: Mapped[str] = mapped_column(String(150), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TicketExtraction(IdMixin, Base):
    """A machine (or manual) reading of a receipt: a proposal until a person validates it."""

    __tablename__ = "ticket_extraction"

    ticket_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which attachment this reading came from (none for a manual proposal).
    attachment_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_attachment.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    fields: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=JSON_LIST_DEFAULT, nullable=False
    )
    corrections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=JSON_LIST_DEFAULT, nullable=False
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    validated_by: Mapped[str | None] = mapped_column(String(150))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BusinessRule(IdMixin, TimestampMixin, Base):
    __tablename__ = "business_rule"

    type_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_type.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    field: Mapped[str] = mapped_column(String(30), nullable=False)
    operator: Mapped[str] = mapped_column(String(12), nullable=False)
    value: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    route_limits: Mapped[dict[str, float] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class IdempotencyRecord(IdMixin, Base):
    """Replay cache of mutating requests, keyed by actor, path and client key."""

    __tablename__ = "idempotency_record"

    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # pending while the first request runs; done once its 2xx answer is stored.
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(100))
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("actor_id", "path", "key", name="uq_idempotency_actor_path_key"),
        Index("ix_idempotency_expires_at", "expires_at"),
    )


# Names resolved live with a subquery, so a renamed role or user shows up everywhere
# and no relationship has to load the role's permissions.
TicketType.default_role_name = column_property(  # type: ignore[attr-defined]
    select(Role.name).where(Role.id == TicketType.default_role_id).scalar_subquery()
)
TicketTypeStep.role_name = column_property(  # type: ignore[attr-defined]
    select(Role.name).where(Role.id == TicketTypeStep.role_id).scalar_subquery()
)
Ticket.assigned_role_name = column_property(  # type: ignore[attr-defined]
    select(Role.name).where(Role.id == Ticket.assigned_role_id).scalar_subquery()
)
Ticket.assigned_user_name = column_property(  # type: ignore[attr-defined]
    select(AppUser.full_name).where(AppUser.id == Ticket.assigned_user_id).scalar_subquery()
)
_Parent = Ticket.__table__.alias("parent_ticket")
Ticket.parent_code = column_property(  # type: ignore[attr-defined]
    select(_Parent.c.code).where(_Parent.c.id == Ticket.parent_id).scalar_subquery()
)
_Child = Ticket.__table__.alias("child_ticket")
Ticket.children_count = column_property(  # type: ignore[attr-defined]
    select(func.count(_Child.c.id)).where(_Child.c.parent_id == Ticket.id).scalar_subquery()
)
TicketType.tickets_count = column_property(  # type: ignore[attr-defined]
    select(func.count(Ticket.id)).where(Ticket.type_id == TicketType.id).scalar_subquery()
)
BusinessRule.type_code = column_property(  # type: ignore[attr-defined]
    select(TicketType.code).where(TicketType.id == BusinessRule.type_id).scalar_subquery()
)
