"""Notices: scoped to the caller's audience IN THE QUERY, with personal read state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import Select

from app.config import database
from app.config.notifications import DEDUPE_WINDOW_SECONDS
from app.models.base import UUID_TYPE
from app.models.notification import Notification, NotificationAudience, NotificationReadMark
from app.config.audit import AuditAction, AuditEntity
from app.config.settings import get_settings
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.base import AuditedRepository
from app.utils.audit_context import audit_is_suppressed


def _twin(kind: str, ticket_id: UUID | None, body: str) -> Select:
    since = datetime.now(timezone.utc) - timedelta(seconds=DEDUPE_WINDOW_SECONDS)
    same_ticket = (
        Notification.ticket_id == ticket_id
        if ticket_id is not None
        else Notification.ticket_id.is_(None)
    )
    return (
        select(Notification.id)
        .where(
            Notification.kind == kind,
            same_ticket,
            Notification.body == body,
            Notification.created_at >= since,
        )
        .limit(1)
    )


def _audience_rows(notification_id: UUID, roles: list[UUID], users: list[UUID]):
    rows = [NotificationAudience(notification_id=notification_id, role_id=r) for r in roles]
    rows += [NotificationAudience(notification_id=notification_id, user_id=u) for u in users]
    return rows


def _created_entry(notification: Notification, roles: list[UUID], users: list[UUID]) -> AuditEntry:
    """The fact that a notice exists. Title and body are NOT recorded: they quote ticket
    data and the trail is read by people who may not be the notice's audience."""
    audience = f"{len(roles)} rol(es), {len(users)} usuario(s)"
    return AuditEntry(
        action=AuditAction.CREATE,
        entity=AuditEntity.NOTIFICATION,
        entity_id=str(notification.id),
        entity_label=notification.kind,
        summary=f"Se generó un aviso «{notification.kind}»"
        + (f" sobre {notification.operation_code}" if notification.operation_code else ""),
        changes=[{"field": "audience", "label": "Destinatarios", "from": None, "to": audience}],
    )


class NotificationRepository(AuditedRepository[Notification]):
    model = Notification
    audit_entity = AuditEntity.NOTIFICATION
    #: Nothing is diffed field by field: creation and bulk reads are written explicitly.
    audit_labels: dict[str, str] = {}

    #: Resolved here (not columns of the model).
    CUSTOM_FILTERS = frozenset({"unread"})

    # --- audience scope ---------------------------------------------------------------------

    def _audience_clause(self):
        """Only notices addressed to the caller's role or to the caller personally."""
        principal = self.service.principal
        return exists().where(
            NotificationAudience.notification_id == Notification.id,
            or_(
                NotificationAudience.role_id == principal.role_id,
                NotificationAudience.user_id == principal.user_id,
            ),
        )

    def _read_flag(self):
        return exists().where(
            NotificationReadMark.notification_id == Notification.id,
            NotificationReadMark.user_id == self.service.principal.user_id,
        )

    def build_list_queryset(self, **kwargs: Any) -> Select:
        query = (
            select(Notification, self._read_flag().label("read"))
            .where(self._audience_clause())
            .order_by(Notification.created_at.desc(), Notification.id)
        )
        if (kwargs.get("filters") or {}).get("unread"):
            query = query.where(~self._read_flag())
        return query

    def apply_list_filters(self, queryset, filters=None, **kwargs):
        plain = {k: v for k, v in (filters or {}).items() if k not in self.CUSTOM_FILTERS}
        return super().apply_list_filters(queryset, filters=plain, **kwargs)

    async def get(self, record_id: Any, filters: dict[str, Any] | None = None):
        """The notice if it is addressed to the caller; a foreign or missing id is None."""
        query = select(Notification, self._read_flag().label("read")).where(
            Notification.id == record_id, self._audience_clause()
        )
        row = (await self.session.execute(query)).first()
        if row is None:
            return None
        notification, read = row
        notification.read = bool(read)
        return notification

    async def get_with_joins(self, record_id: Any, joins=None, filters=None):
        return await self.get(record_id)

    async def unread_count(self) -> int:
        query = (
            select(func.count())
            .select_from(Notification)
            .where(self._audience_clause(), ~self._read_flag())
        )
        return int((await self.session.execute(query)).scalar_one())

    # --- read state ---------------------------------------------------------------------------

    async def mark_read(self, notification_id: UUID) -> None:
        """Idempotent: marking twice leaves one row."""
        insert = pg_insert(NotificationReadMark).values(
            notification_id=notification_id, user_id=self.service.principal.user_id
        )
        await self.session.execute(insert.on_conflict_do_nothing())

    async def mark_unread(self, notification_id: UUID) -> None:
        await self.session.execute(
            delete(NotificationReadMark).where(
                NotificationReadMark.notification_id == notification_id,
                NotificationReadMark.user_id == self.service.principal.user_id,
            )
        )

    async def mark_all_read(self) -> int:
        """Every unread notice of the caller. Returns how many were pending."""
        user_id = self.service.principal.user_id
        pending = select(Notification.id, literal(user_id, type_=UUID_TYPE)).where(
            self._audience_clause(), ~self._read_flag()
        )
        insert = (
            pg_insert(NotificationReadMark)
            .from_select(["notification_id", "user_id"], pending)
            .on_conflict_do_nothing()
        )
        marked = int((await self.session.execute(insert)).rowcount or 0)
        if marked:
            principal = self.service.principal
            await self.emit(
                AuditEntry(
                    action=AuditAction.UPDATE,
                    entity=AuditEntity.NOTIFICATION,
                    entity_id=str(principal.user_id),
                    entity_label=principal.email,
                    summary=f"{principal.full_name} marcó {marked} aviso(s) como leídos",
                    changes=[
                        {"field": "read", "label": "Avisos leídos", "from": None, "to": marked}
                    ],
                )
            )
        return marked

    # --- writes ---------------------------------------------------------------------------------

    async def is_duplicate(self, kind: str, ticket_id: UUID | None, body: str) -> bool:
        return (await self.session.execute(_twin(kind, ticket_id, body))).first() is not None

    async def add(
        self, values: dict[str, Any], roles: list[UUID], users: list[UUID]
    ) -> Notification:
        notification = Notification(**values)
        self.session.add(notification)
        await self.session.flush()
        self.session.add_all(_audience_rows(notification.id, roles, users))
        await self.session.flush()
        await self.session.refresh(notification, ["audiences"])
        await self.emit(_created_entry(notification, roles, users))
        return notification

    async def add_independent(
        self, values: dict[str, Any], roles: list[UUID], users: list[UUID]
    ) -> bool:
        """Persist in a transaction of its own, so a rolled-back request keeps the notice
        (a refused close attempt). Returns False when an equal notice is still fresh."""
        async with database.AsyncSessionFactory() as session:
            duplicate = (
                await session.execute(
                    _twin(values["kind"], values.get("ticket_id"), values["body"])
                )
            ).first()
            if duplicate is not None:
                return False
            notification = Notification(**values)
            session.add(notification)
            await session.flush()
            session.add_all(_audience_rows(notification.id, roles, users))
            if not audit_is_suppressed():
                # Same transaction as the notice: either both exist or neither does.
                await AuditEventRepository(session).append(
                    _created_entry(notification, roles, users)
                )
            await session.commit()
            return True

    # --- retention ------------------------------------------------------------------------------

    async def purge_older_than(self, cutoff: datetime) -> int:
        """Delete notices created before `cutoff` (audiences and read marks cascade).
        Leaves one summary event; the notices themselves are not copied to the trail."""
        result = await self.session.execute(
            delete(Notification).where(Notification.created_at < cutoff)
        )
        purged = int(result.rowcount or 0)
        if purged:
            await self.emit(
                AuditEntry(
                    action=AuditAction.DELETE,
                    entity=AuditEntity.NOTIFICATION,
                    entity_id="retention",
                    entity_label="retención",
                    summary=f"Purgó {purged} aviso(s) anteriores a {cutoff:%Y-%m-%d}",
                    changes=[
                        {
                            "field": "retention_days",
                            "label": "Retención (días)",
                            "from": None,
                            "to": get_settings().NOTIFICATION_RETENTION_DAYS,
                        }
                    ],
                )
            )
        return purged
