"""Reconciliation: closed periods and the justifications that allow closing them."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUID_TYPE, Base, IdMixin


class ReconciliationPeriod(IdMixin, Base):
    """A closed month (`YYYY-MM`). A closed period is never reopened, so there is no status
    column: the row exists or it does not."""

    __tablename__ = "reconciliation_period"

    period: Mapped[str] = mapped_column(String(7), nullable=False, unique=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_by_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    closed_by_name: Mapped[str] = mapped_column(String(150), nullable=False)
    closed_by_role: Mapped[str] = mapped_column(String(120), nullable=False)
    rows_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_base: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    acknowledged_count: Mapped[int] = mapped_column(Integer, nullable=False)


class ReconciliationAcknowledgement(IdMixin, Base):
    """A person's justification of one difference of one period."""

    __tablename__ = "reconciliation_acknowledgement"

    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    actor_name: Mapped[str] = mapped_column(String(150), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(120), nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("period", "key", name="uq_reconciliation_ack_period_key"),)
