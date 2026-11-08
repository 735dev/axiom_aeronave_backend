from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEventRead(BaseModel):
    id: UUID
    at: datetime
    actor_id: str
    actor_name: str
    actor_role: str
    action: str
    entity: str
    entity_id: str
    entity_label: str
    summary: str
    changes: list[dict[str, Any]] = []
    ip: str | None = None
    user_agent: str | None = None

    model_config = ConfigDict(from_attributes=True)
