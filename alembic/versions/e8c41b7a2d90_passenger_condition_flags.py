"""passenger condition flags

Revision ID: e8c41b7a2d90
Revises: d7a31c88e4b2
Create Date: 2026-11-18 16:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e8c41b7a2d90"
down_revision: str | None = "d7a31c88e4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("is_minor", "has_disability", "needs_wheelchair", "has_minor_permit"):
        op.add_column(
            "ticket_passenger",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    for name in ("has_minor_permit", "needs_wheelchair", "has_disability", "is_minor"):
        op.drop_column("ticket_passenger", name)
