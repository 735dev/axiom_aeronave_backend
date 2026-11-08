"""Ticket types and their definition (fields, statuses, steps)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import false, func, select, true
from sqlalchemy.sql import Select

from app.config.audit import AuditAction, AuditEntity
from app.models.ticket import (
    Ticket,
    TicketType,
    TicketTypeField,
    TicketTypeStatus,
    TicketTypeStep,
)
from app.repositories.audit_trail import AuditEntry
from app.repositories.base import AuditedRepository
from app.utils.ticket_access import visible_type_codes_for_definitions


class TicketTypeRepository(AuditedRepository[TicketType]):
    model = TicketType
    default_ordering = ("name",)
    audit_entity = AuditEntity.TICKET_TYPE
    audit_labels = {
        "name": "Nombre",
        "description": "Descripción",
        "is_root": "Es raíz",
        "child_type_codes": "Tipos hijos",
        "blocks_parent_close": "Bloquea el cierre del padre",
        "relation_kinds": "Relaciones",
        "is_active": "Activo",
    }

    def audit_label(self, obj: TicketType) -> str:
        return f"{obj.name} ({obj.code})"

    # --- row-level scope: a caller only gets the types they can work with -------------

    def _visible_clause(self):
        codes = visible_type_codes_for_definitions(self.service.principal.permissions)
        if codes is None:
            return true()
        if not codes:
            return false()
        return TicketType.code.in_(sorted(codes))

    def build_list_queryset(self, **kwargs: Any) -> Select:
        return super().build_list_queryset(**kwargs).where(self._visible_clause())

    async def get(self, record_id: Any, filters: dict[str, Any] | None = None):
        return await self._get_one(conditions=[TicketType.id == record_id, self._visible_clause()])

    async def get_with_joins(self, record_id: Any, joins=None, filters=None):
        return await self.get(record_id)

    # --- lookups (unscoped: internal rules, never exposed as such) ---------------------

    async def get_by_code(self, code: str) -> TicketType | None:
        query = select(TicketType).where(TicketType.code == code)
        return (await self.session.execute(query)).scalars().first()

    async def get_internal(self, type_id: Any) -> TicketType | None:
        return await self.session.get(TicketType, type_id)

    async def by_codes(self, codes: list[str]) -> dict[str, TicketType]:
        if not codes:
            return {}
        query = select(TicketType).where(TicketType.code.in_(codes))
        return {t.code: t for t in (await self.session.execute(query)).scalars().all()}

    async def code_taken(self, code: str) -> bool:
        query = select(TicketType.id).where(TicketType.code == code).limit(1)
        return (await self.session.execute(query)).first() is not None

    async def has_tickets(self, type_id: Any) -> bool:
        query = select(Ticket.id).where(Ticket.type_id == type_id).limit(1)
        return (await self.session.execute(query)).first() is not None

    async def status_codes_in_use(self, type_id: Any) -> set[str]:
        query = (
            select(TicketTypeStatus.code)
            .join(Ticket, Ticket.status_id == TicketTypeStatus.id)
            .where(Ticket.type_id == type_id)
            .distinct()
        )
        return set((await self.session.execute(query)).scalars().all())

    async def types_listing_child(self, code: str, exclude_id: Any = None) -> list[TicketType]:
        """Types that name `code` among their allowed children."""
        result = await self.session.execute(select(TicketType).order_by(TicketType.code))
        return [
            t
            for t in result.scalars().all()
            if code in (t.child_type_codes or []) and t.id != exclude_id
        ]

    async def count_by_role(self, role_id: Any) -> int:
        query = select(func.count(TicketTypeStep.id)).where(TicketTypeStep.role_id == role_id)
        return int((await self.session.execute(query)).scalar_one())

    # --- writes -----------------------------------------------------------------------

    async def create_type(
        self,
        values: dict[str, Any],
        fields: list[dict[str, Any]],
        statuses: list[dict[str, Any]],
        steps: list[dict[str, Any]],
    ) -> TicketType:
        record = TicketType(**values)
        record.fields = [TicketTypeField(**f) for f in fields]
        record.statuses = [TicketTypeStatus(**s) for s in statuses]
        record.steps = [TicketTypeStep(**s) for s in steps]
        created = await self.create(record)
        await self.emit(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.TICKET_TYPE,
                entity_id=str(created.id),
                entity_label=self.audit_label(created),
                summary=(
                    f"Definió {len(fields)} campo(s), {len(statuses)} estado(s) y "
                    f"{len(steps)} paso(s) en {created.name}"
                ),
                changes=[
                    _keys_change("fields", "Campos", [], [f["key"] for f in fields]),
                    _keys_change("statuses", "Estados", [], [s["code"] for s in statuses]),
                    _keys_change("steps", "Pasos", [], [s["code"] for s in steps]),
                ],
            )
        )
        return created

    async def replace_collections(
        self,
        record: TicketType,
        fields: list[dict[str, Any]] | None,
        statuses: list[dict[str, Any]] | None,
        steps: list[dict[str, Any]] | None,
    ) -> TicketType:
        """Replace whole collections, keeping the row (and id) of every key that stays.

        Status rows are referenced by tickets, so they are updated in place by code.
        """
        changes: list[dict[str, Any]] = []
        if fields is not None:
            old = [f.key for f in record.fields]
            # A role may move from one field to another: free them first so the unique
            # (type, role) constraint never sees both at once.
            for row in record.fields:
                row.role = None
            await self.session.flush()
            _sync(record.fields, fields, "key", TicketTypeField)
            changes.append(_keys_change("fields", "Campos", old, [f["key"] for f in fields]))
        if statuses is not None:
            old = [s.code for s in record.statuses]
            _sync(record.statuses, statuses, "code", TicketTypeStatus)
            changes.append(_keys_change("statuses", "Estados", old, [s["code"] for s in statuses]))
        if steps is not None:
            old = [s.code for s in record.steps]
            _sync(record.steps, steps, "code", TicketTypeStep)
            changes.append(_keys_change("steps", "Pasos", old, [s["code"] for s in steps]))
        if not changes:
            return record
        await self.session.flush()
        await self.session.refresh(record)
        meaningful = [c for c in changes if c["from"] != c["to"]]
        if meaningful:
            await self.emit(
                AuditEntry(
                    action=AuditAction.UPDATE,
                    entity=AuditEntity.TICKET_TYPE,
                    entity_id=str(record.id),
                    entity_label=self.audit_label(record),
                    summary=f"Cambió la definición de {record.name}",
                    changes=meaningful,
                )
            )
        return record

    async def remove_from_children(self, code: str, parents: list[TicketType]) -> None:
        for parent in parents:
            parent.child_type_codes = [c for c in parent.child_type_codes if c != code]
        await self.session.flush()


def _keys_change(field: str, label: str, old: list[str], new: list[str]) -> dict[str, Any]:
    return {
        "field": field,
        "label": label,
        "from": ", ".join(old) if old else None,
        "to": ", ".join(new) if new else None,
    }


def _sync(current: list[Any], wanted: list[dict[str, Any]], key: str, cls: type) -> None:
    """Make the `current` collection match `wanted`, keyed by `key`, preserving rows."""
    existing = {getattr(row, key): row for row in current}
    desired = [w[key] for w in wanted]
    for row in list(current):
        if getattr(row, key) not in desired:
            current.remove(row)
    result = []
    for item in wanted:
        row = existing.get(item[key])
        if row is None:
            row = cls(**item)
        else:
            for name, value in item.items():
                setattr(row, name, value)
        result.append(row)
    current[:] = result
