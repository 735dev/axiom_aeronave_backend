"""Append-only access to the audit trail.

This repository deliberately exposes no way to modify or remove an event: `update`,
`delete`, `save` and `hard_delete` raise. Events enter through `append` (inside the
request transaction, so the event and the change commit or roll back together) or
`append_independent` (its own transaction, for facts that must survive a rollback,
such as a failed login).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi_basekit.aio.sqlalchemy.repository.base import BaseRepository
from sqlalchemy import Text, cast, or_, select
from sqlalchemy.sql import Select

from app.config import database
from app.config.audit import USER_AGENT_MAX_LENGTH, AuditAction, AuditEntity
from app.exceptions.admin import AuditError
from app.models.audit import AuditEvent
from app.utils.audit_context import Actor, current_actor, current_origin
from app.utils.search import LIKE_ESCAPE, escape_like


@dataclass
class AuditEntry:
    action: AuditAction
    entity: AuditEntity
    entity_id: str
    entity_label: str
    summary: str
    changes: list[dict[str, Any]] = field(default_factory=list)
    # Explicit actor for facts with no session yet (a failed login). Otherwise the
    # actor is the one bound from the verified token.
    actor: Actor | None = None


def _build_event(entry: AuditEntry) -> AuditEvent:
    actor = entry.actor or current_actor()
    origin = current_origin()
    user_agent = origin.user_agent[:USER_AGENT_MAX_LENGTH] if origin.user_agent else None
    return AuditEvent(
        at=datetime.now(timezone.utc),
        actor_id=actor.id,
        actor_name=actor.name,
        actor_role=actor.role,
        action=entry.action.value,
        entity=entry.entity.value,
        entity_id=entry.entity_id,
        entity_label=entry.entity_label,
        summary=entry.summary,
        changes=entry.changes,
        ip=origin.ip,
        user_agent=user_agent,
    )


class AuditEventRepository(BaseRepository[AuditEvent]):
    model = AuditEvent

    def build_list_queryset(self, **kwargs: Any) -> Select:
        query = select(AuditEvent).order_by(AuditEvent.seq.desc())
        term = (kwargs.get("search") or "").strip()
        if term:
            pattern = f"%{escape_like(term)}%"
            query = query.where(
                or_(
                    *(
                        column.ilike(pattern, escape=LIKE_ESCAPE)
                        for column in (
                            AuditEvent.summary,
                            AuditEvent.entity_label,
                            AuditEvent.actor_name,
                            AuditEvent.actor_role,
                            cast(AuditEvent.changes, Text),
                        )
                    )
                )
            )
        return query

    async def export_rows(
        self, filters: dict[str, Any], search: str | None, limit: int
    ) -> list[AuditEvent]:
        """Oldest first, at most `limit` rows (the caller asks for one more to detect overflow)."""
        query = self.apply_list_filters(self.build_list_queryset(search=search), filters=filters)
        query = query.order_by(None).order_by(AuditEvent.seq.asc()).limit(limit)
        return list((await self.session.execute(query)).scalars().all())

    async def append(self, entry: AuditEntry) -> AuditEvent:
        event = _build_event(entry)
        self.session.add(event)
        await self.session.flush()
        return event

    async def append_independent(self, entry: AuditEntry) -> None:
        """Persist in a separate transaction, so a rollback of the request keeps the fact."""
        async with database.AsyncSessionFactory() as session:
            session.add(_build_event(entry))
            await session.commit()

    async def update(self, *args: Any, **kwargs: Any):
        raise AuditError("append_only", "La bitácora es de solo escritura: no admite cambios.")

    async def delete(self, *args: Any, **kwargs: Any):
        raise AuditError("append_only", "La bitácora es de solo escritura: no admite borrados.")

    async def save(self, *args: Any, **kwargs: Any):
        raise AuditError("append_only", "La bitácora es de solo escritura: no admite cambios.")

    async def hard_delete(self, *args: Any, **kwargs: Any):
        raise AuditError("append_only", "La bitácora es de solo escritura: no admite borrados.")
