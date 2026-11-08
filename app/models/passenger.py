"""Passengers of an operation's manifest."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUID_TYPE, Base, IdMixin, TimestampMixin


class TicketPassenger(IdMixin, TimestampMixin, Base):
    __tablename__ = "ticket_passenger"

    ticket_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    document: Mapped[str] = mapped_column(String(40), nullable=False)
    nationality: Mapped[str | None] = mapped_column(String(60))
    # Reference to the stored passport image (a support of the same operation). Only the
    # reference lives here: the file itself belongs to the attachments module.
    passport_ref: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    # Comparable forms, computed by the server: accent-free lowercase name and a document
    # without dots, spaces or dashes in uppercase. Searches only ever touch these.
    name_key: Mapped[str] = mapped_column(String(200), nullable=False)
    document_key: Mapped[str] = mapped_column(String(40), nullable=False)

    #: Set by the retention policy once name, document and passport reference were erased.
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("ticket_id", "document_key", name="uq_ticket_passenger_ticket_document"),
        Index("ix_ticket_passenger_document_key", "document_key"),
        Index("ix_ticket_passenger_name_key", "name_key"),
    )
