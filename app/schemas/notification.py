"""Shapes of the notifications module."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.common import Page, RequestModel


class NotificationRead(BaseModel):
    id: UUID
    kind: str
    title: str
    body: str
    ticket_id: UUID | None = None
    operation_code: str | None = None
    #: Names of the roles the notice was addressed to (display only).
    audience: list[str] = []
    created_at: datetime
    #: The caller's own read state, never anybody else's.
    read: bool = False

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _from_notification(cls, obj: Any) -> Any:
        if isinstance(obj, dict) or not hasattr(obj, "audiences"):
            return obj
        return {
            "id": obj.id,
            "kind": obj.kind,
            "title": obj.title,
            "body": obj.body,
            "ticket_id": obj.ticket_id,
            "operation_code": obj.operation_code,
            "audience": sorted({a.role.name for a in obj.audiences if a.role is not None}),
            "created_at": obj.created_at,
            "read": bool(getattr(obj, "read", False)),
        }


class NotificationPage(Page[NotificationRead]):
    """The contract envelope plus the caller's unread total (not the page's)."""

    unread: int = 0


class NotificationUpdate(RequestModel):
    read: bool = True


class ReadAllResult(BaseModel):
    marked: int
    unread: int
