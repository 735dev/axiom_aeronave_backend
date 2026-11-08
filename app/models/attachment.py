"""Files attached to tickets. The bytes live in Blackbox; the ticket keeps the reference."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import UUID_TYPE, Base, IdMixin

if TYPE_CHECKING:
    from app.models.ticket import Ticket, TicketExtraction


class TicketAttachment(IdMixin, Base):
    __tablename__ = "ticket_attachment"

    ticket_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Sanitized name the person gave the file (display only; never used as a path).
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Canonical MIME type of the content we detected, not the one the client claimed.
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    field_key: Mapped[str | None] = mapped_column(String(60))
    #: Ordinal inside (ticket, document_type). Deleted rows keep theirs, so a name is never reused.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    folder: Mapped[str] = mapped_column(String(120), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The client's Idempotency-Key for this upload (scoped to ticket and uploader).
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    uploaded_by_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    uploaded_by: Mapped[str] = mapped_column(String(150), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(150))

    #: Loaded only where a listing asks for it (`selectinload`): never lazily.
    ticket: Mapped["Ticket"] = relationship(
        "Ticket", foreign_keys=[ticket_id], lazy="raise", viewonly=True
    )
    extraction: Mapped["TicketExtraction | None"] = relationship(
        "TicketExtraction",
        primaryjoin="TicketAttachment.id == TicketExtraction.attachment_id",
        lazy="selectin",
        uselist=False,
        viewonly=True,
    )

    __table_args__ = (
        Index(
            "uq_ticket_attachment_idempotency",
            "ticket_id",
            "uploaded_by_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_ticket_attachment_document", "document_id"),
    )


class AttachmentUpload(IdMixin, Base):
    """A reserved upload slot: the file is on its way to Blackbox and not yet linked.

    Written (and committed) while the ticket row is locked, BEFORE the external call, so the
    call itself runs without any database lock. It reserves the ordinal of the archived name
    and lets a retry with the same Idempotency-Key resume exactly where a crash left it. It is
    deleted in the same transaction that creates the `TicketAttachment`; on a clean failure
    it is deleted at once, and a crash leaves it for the maintenance sweep.
    """

    __tablename__ = "attachment_upload"

    ticket_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploader_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    #: What Blackbox deduplicates on (deterministic when the client sent a key).
    client_key: Mapped[str] = mapped_column(String(64), nullable=False)
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    field_key: Mapped[str | None] = mapped_column(String(60))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "ticket_id", "document_type", "sequence", name="uq_attachment_upload_slot"
        ),
        Index(
            "uq_attachment_upload_idempotency",
            "ticket_id",
            "uploader_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_attachment_upload_created_at", "created_at"),
    )
