"""Readings of receipts, shared by tickets and attachments."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExtractedFieldRead(BaseModel):
    key: str
    label: str
    value: str | int | float
    confidence: str


class ExtractionRead(BaseModel):
    id: UUID
    ticket_id: UUID
    source: str
    status: str
    fields: list[ExtractedFieldRead] = []
    corrections: list[dict[str, Any]] = []
    created_at: datetime
    validated_by: str | None = None
    validated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
