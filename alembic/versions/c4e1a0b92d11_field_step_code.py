"""field step code

Revision ID: c4e1a0b92d11
Revises: 150b73ee31d0
Create Date: 2026-11-11 12:40:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c4e1a0b92d11"
down_revision: str | None = "150b73ee31d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticket_type_field", sa.Column("step_code", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column("ticket_type_field", "step_code")
