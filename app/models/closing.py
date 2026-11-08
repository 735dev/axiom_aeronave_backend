"""The seal of a signed closing: written once when an operation closes, never updated."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.models.base import UUID_TYPE, Base, IdMixin
from app.models.ticket import JSON_OBJECT_DEFAULT, Ticket


class TicketClosing(IdMixin, Base):
    __tablename__ = "ticket_closing"

    ticket_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    signed_by_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    signed_by: Mapped[str] = mapped_column(String(150), nullable=False)
    signed_role: Mapped[str] = mapped_column(String(120), nullable=False)
    # Copy of the signer's image at the moment of signing: later changes never touch it.
    signature_url: Mapped[str] = deferred(mapped_column(Text, nullable=False))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    totals: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=JSON_OBJECT_DEFAULT, nullable=False
    )
    files_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    folder: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


# One-to-one read side, attached here so tickets.py needs no knowledge of the closing module.
Ticket.closing_record = relationship(  # type: ignore[attr-defined]
    TicketClosing, lazy="selectin", uselist=False, viewonly=True
)
