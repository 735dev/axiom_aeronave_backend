"""Event notices, their audience (roles and/or users) and the per-user read state."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.access import Role
from app.models.base import UUID_TYPE, Base, IdMixin


class Notification(IdMixin, Base):
    __tablename__ = "notification"

    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(600), nullable=False)
    #: Plain column, not a foreign key: a close-blocked notice is written from its own
    #: transaction while the request still holds the ticket row locked.
    ticket_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    operation_code: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    audiences: Mapped[list["NotificationAudience"]] = relationship(
        "NotificationAudience",
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_notification_created_at", "created_at"),
        Index("ix_notification_ticket_id", "ticket_id"),
    )


class NotificationAudience(IdMixin, Base):
    """One recipient of a notice: a whole role or one user."""

    __tablename__ = "notification_audience"

    notification_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("notification.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("role.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )

    role: Mapped[Role | None] = relationship(Role, lazy="selectin")

    __table_args__ = (CheckConstraint("num_nonnulls(role_id, user_id) = 1", name="one_recipient"),)


class NotificationReadMark(Base):
    """Reading is personal: one row per (notice, user)."""

    __tablename__ = "notification_read"

    notification_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("notification.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        UUID_TYPE, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        PrimaryKeyConstraint("notification_id", "user_id", name="pk_notification_read"),
        Index("ix_notification_read_user_id", "user_id"),
    )
