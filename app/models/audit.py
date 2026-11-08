"""Append-only audit trail.

There is no code path that updates or deletes a row: the repository exposes no such
method, the ORM refuses the operation, and in production the application role has no
UPDATE/DELETE grant on this table (see docs/AUDITORIA.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


from sqlalchemy import BigInteger, DateTime, Identity, Index, String, Text, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base, IdMixin


class AuditEvent(IdMixin, Base):
    __tablename__ = "audit_event"

    # Monotonic order of insertion; the listing sorts by it so ties on `at` are stable.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Not a foreign key: it may be 'system', 'anonymous' or a user that is later removed.
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Name and role are frozen at the moment of the event, never resolved by a join.
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    entity: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_label: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index("ix_audit_event_entity", "entity", "entity_id", "seq"),
        Index("ix_audit_event_actor", "actor_id", "seq"),
        Index("ix_audit_event_action", "action", "seq"),
        Index("ix_audit_event_at", "at"),
    )


@event.listens_for(AuditEvent, "before_update")
def _refuse_update(mapper, connection, target) -> None:
    raise RuntimeError("audit_event is append-only: updates are not allowed")


@event.listens_for(AuditEvent, "before_delete")
def _refuse_delete(mapper, connection, target) -> None:
    raise RuntimeError("audit_event is append-only: deletes are not allowed")
