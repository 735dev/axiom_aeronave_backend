"""status advance rules

Revision ID: d7a31c88e4b2
Revises: c4e1a0b92d11
Create Date: 2026-11-11 17:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d7a31c88e4b2"
down_revision: str | None = "c4e1a0b92d11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ticket_type_status",
        sa.Column("advance_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ticket_type_status", "advance_rules")
