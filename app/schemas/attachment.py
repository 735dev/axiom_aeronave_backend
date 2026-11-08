"""Shapes of the attachments module. Requests are explicit; the model is never the input."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.config.attachments import FOLDER_PREFIX
from app.schemas.reading import ExtractedFieldRead, ExtractionRead


class AttachmentStorage(BaseModel):
    provider: str
    document_id: str
    folder: str
    filename: str


class AttachmentRead(BaseModel):
    id: UUID
    name: str
    mime: str
    size: int
    document_type: str
    storage: AttachmentStorage
    uploaded_by: str
    uploaded_at: datetime
    extraction: ExtractionRead | None = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _from_attachment(cls, obj: Any) -> Any:
        if isinstance(obj, dict) or not hasattr(obj, "document_id"):
            return obj
        return {
            "id": obj.id,
            "name": obj.name,
            "mime": obj.mime,
            "size": obj.size,
            "document_type": obj.document_type,
            "storage": {
                "provider": obj.provider,
                "document_id": obj.document_id,
                "folder": f"{FOLDER_PREFIX}{obj.folder}",
                "filename": obj.filename,
            },
            "uploaded_by": obj.uploaded_by,
            "uploaded_at": obj.uploaded_at,
            "extraction": obj.extraction,
        }


class AttachmentLink(BaseModel):
    """A short-lived signed link. It is built per request and never stored."""

    url: str
    expires_at: datetime


class PendingAttachmentRead(BaseModel):
    ticket_id: UUID
    ticket_code: str
    ticket_title: str
    type_name: str
    parent_code: str | None = None
    file: AttachmentRead

    @model_validator(mode="before")
    @classmethod
    def _from_attachment(cls, obj: Any) -> Any:
        if isinstance(obj, dict) or not hasattr(obj, "ticket"):
            return obj
        ticket = obj.ticket
        return {
            "ticket_id": ticket.id,
            "ticket_code": ticket.code,
            "ticket_title": ticket.title,
            "type_name": ticket.type.name,
            "parent_code": ticket.parent_code,
            "file": obj,
        }


class ReadingPreview(BaseModel):
    """What the reader proposes for a receipt, before any ticket exists. Nothing is saved."""

    status: str
    fields: list[ExtractedFieldRead] = []
    corrections: list[dict[str, Any]] = []
    review_required: list[str] = []
