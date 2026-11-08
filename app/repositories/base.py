"""Project repository bases.

`ProjectRepository` adds what every listing needs (stable ordering, an unpaginated
read, a scoping hook). `AuditedRepository` is the single place through which every
write passes: any repository that extends it emits an audit event, with a field-level
diff and the actor taken from the request token, without the endpoint or the service
having to remember to do so.
"""

from __future__ import annotations

from typing import Any, ClassVar, TypeVar

from fastapi_basekit.aio.sqlalchemy.repository.base import BaseRepository
from fastapi_basekit.exceptions.api_exceptions import NotFoundException
from sqlalchemy import inspect, select
from sqlalchemy.sql import Select

from app.config.audit import ENTITY_SUBJECT, AuditAction, AuditEntity
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.utils.audit_context import audit_is_suppressed
from app.utils.audit_diff import diff_values
from app.utils.search import escape_like

ModelT = TypeVar("ModelT")


class ProjectRepository(BaseRepository[ModelT]):
    #: Attribute names, '-' prefix for descending. The id is always the last tie-breaker.
    default_ordering: ClassVar[tuple[str, ...]] = ()

    def build_list_queryset(self, **kwargs: Any) -> Select:
        query = select(self.model)
        for name in self.default_ordering:
            column = getattr(self.model, name.lstrip("-"))
            query = query.order_by(column.desc() if name.startswith("-") else column.asc())
        return query.order_by(self.model.id)

    def _build_search_condition(self, search, search_fields):
        # basekit interpolates the term into ILIKE as-is, so `%` and `_` would act as
        # wildcards. Backslash is PostgreSQL's default LIKE escape character.
        return super()._build_search_condition(
            escape_like(search) if search else search, search_fields
        )

    def scope_filters(self) -> dict[str, Any]:
        """Row-level ownership filter applied to every read-by-id, update and delete.

        Override in a repository whose rows belong to an owner. basekit's `update` and
        `delete` take no filters, so this hook is applied in the query by the overrides
        below: a foreign id is indistinguishable from a missing one (404, never 403).
        """
        return {}

    async def get_scoped(self, record_id: Any) -> ModelT | None:
        return await self.get(record_id, filters=self.scope_filters() or None)

    async def list_all(self, filters: dict[str, Any] | None = None) -> list[ModelT]:
        """Whole result set, for reference lists the client loads in one call."""
        query = self.apply_list_filters(
            self.build_list_queryset(), filters={**self.scope_filters(), **(filters or {})}
        )
        result = await self.session.execute(query)
        return list(result.unique().scalars().all())

    def _capture_before_flush(self, record: ModelT) -> Any:
        """Hook: read whatever must be known before the flush clears attribute history."""
        return None

    async def _after_update(self, record: ModelT, captured: Any) -> None:
        """Hook: runs once the update is flushed and the record refreshed."""

    async def update(self, record_id: Any, update_data: dict[str, Any]) -> ModelT:
        """Scoped update. Unlike basekit's, an explicit None clears the column."""
        record = await self.get_scoped(record_id)
        if record is None:
            raise NotFoundException(message=f"{self.model.__name__} no encontrado")
        for key, value in update_data.items():
            if hasattr(record, key):
                setattr(record, key, value)
        captured = self._capture_before_flush(record)
        await self.session.flush()
        await self.session.refresh(record)
        await self._after_update(record, captured)
        return record

    async def delete(self, record_id: Any, model: Any = None) -> bool:
        record = await self.get_scoped(record_id)
        if record is None:
            raise NotFoundException(message=f"{self.model.__name__} no encontrado")
        await self.hard_delete(record)
        return True


class AuditedRepository(ProjectRepository[ModelT]):
    #: Entity name stored in the trail.
    audit_entity: ClassVar[AuditEntity]
    #: Tracked columns and their Spanish labels. Anything not listed is never recorded,
    #: which is how password hashes and images stay out of the trail.
    audit_labels: ClassVar[dict[str, str]] = {}

    def __init__(self, db):
        super().__init__(db)
        self._events = AuditEventRepository(db)

    def audit_label(self, obj: ModelT) -> str:
        """Human label frozen into the event ('TK-0002', an email, a role name)."""
        for name in ("name", "code", "email"):
            value = getattr(obj, name, None)
            if value:
                return str(value)
        return str(obj.id)  # type: ignore[attr-defined]

    async def emit(self, entry: AuditEntry) -> None:
        """Append an event inside the current transaction, unless auditing is suppressed."""
        if audit_is_suppressed():
            return
        await self._events.append(entry)

    async def emit_independent(self, entry: AuditEntry) -> None:
        """Append in its own transaction, for facts that must outlive a rolled-back request
        (an attempt refused with a 4xx aborts the request transaction)."""
        if audit_is_suppressed():
            return
        await self._events.append_independent(entry)

    def _subject(self) -> str:
        return ENTITY_SUBJECT[self.audit_entity]

    def _created_changes(self, obj: ModelT) -> list[dict[str, Any]]:
        after = {f: getattr(obj, f, None) for f in self.audit_labels}
        return diff_values({}, after, self.audit_labels)

    def _pending_changes(self, obj: ModelT) -> list[dict[str, Any]]:
        """Diff of a persistent object built from the ORM attribute history.

        Must run before the flush: afterwards the history is cleared.
        """
        state = inspect(obj)
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        for name in self.audit_labels:
            history = state.attrs[name].history
            if not history.has_changes():
                continue
            before[name] = history.deleted[0] if history.deleted else None
            after[name] = history.added[0] if history.added else getattr(obj, name, None)
        return diff_values(before, after, self.audit_labels)

    async def _emit_change(
        self,
        action: AuditAction,
        obj: ModelT,
        summary: str,
        changes: list[dict[str, Any]],
        label: str | None = None,
    ) -> None:
        await self.emit(
            AuditEntry(
                action=action,
                entity=self.audit_entity,
                entity_id=str(obj.id),  # type: ignore[attr-defined]
                entity_label=label or self.audit_label(obj),
                summary=summary,
                changes=changes,
            )
        )

    async def _emit_created(self, obj: ModelT) -> None:
        label = self.audit_label(obj)
        await self._emit_change(
            AuditAction.CREATE,
            obj,
            f"Creó {self._subject()} {label}",
            self._created_changes(obj),
        )

    async def _emit_updated(self, obj: ModelT, changes: list[dict[str, Any]]) -> None:
        if not changes:
            return
        await self._emit_change(
            AuditAction.UPDATE,
            obj,
            f"Editó {len(changes)} campo(s) de {self.audit_label(obj)}",
            changes,
        )

    async def _emit_deleted(self, obj: ModelT, label: str) -> None:
        snapshot = {f: getattr(obj, f, None) for f in self.audit_labels}
        changes = [
            {**c, "from": c["to"], "to": None} for c in diff_values({}, snapshot, self.audit_labels)
        ]
        await self._emit_change(
            AuditAction.DELETE, obj, f"Eliminó {self._subject()} {label}", changes, label
        )

    async def create(self, obj_in: ModelT | dict[str, Any]) -> ModelT:
        created = await super().create(obj_in)
        await self._emit_created(created)
        return created

    def _capture_before_flush(self, record: ModelT) -> list[dict[str, Any]]:
        return self._pending_changes(record)

    async def _after_update(self, record: ModelT, captured: list[dict[str, Any]]) -> None:
        await self._emit_updated(record, captured)

    async def save(self, obj: ModelT) -> ModelT:
        is_new = inspect(obj).transient or inspect(obj).pending
        changes = [] if is_new else self._pending_changes(obj)
        saved = await super().save(obj)
        await self.session.refresh(saved)
        if is_new:
            await self._emit_created(saved)
        else:
            await self._emit_updated(saved, changes)
        return saved

    async def hard_delete(self, obj: ModelT) -> None:
        label = self.audit_label(obj)
        await super().hard_delete(obj)
        await self._emit_deleted(obj, label)
