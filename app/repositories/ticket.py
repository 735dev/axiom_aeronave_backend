"""Tickets: scoped reads, numbering counters, decisions, comments and the audit facts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Integer, and_, cast, false, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import Select

from app.config.audit import AuditAction, AuditEntity
from app.config.tickets import (
    COUNTER_CONTROL_SERIES,
    COUNTER_TICKET_SERIES,
    DECISION_LABELS,
    TICKET_CODE_PREFIX,
)
from app.models.ticket import (
    Ticket,
    TicketComment,
    TicketCounter,
    TicketDecision,
    TicketType,
    TicketTypeStatus,
)
from app.repositories.audit_trail import AuditEntry
from app.repositories.base import AuditedRepository
from app.utils.audit_diff import diff_values
from app.utils.ticket_access import viewable_type_codes


class TicketRepository(AuditedRepository[Ticket]):
    model = Ticket
    audit_entity = AuditEntity.TICKET
    audit_labels = {"title": "Título", "payload": "Datos", "due_at": "Vencimiento"}

    #: Filters this repository resolves itself (they are not columns of the model).
    CUSTOM_FILTERS = frozenset({"mine"})

    def audit_label(self, obj: Ticket) -> str:
        return obj.code

    # --- row-level scope --------------------------------------------------------------

    def _visible_clause(self):
        """Tickets of the types the caller may view. Fails closed without a principal."""
        codes = viewable_type_codes(self.service.principal.permissions)
        if codes is None:
            return true()
        if not codes:
            return false()
        return Ticket.type_id.in_(select(TicketType.id).where(TicketType.code.in_(sorted(codes))))

    def _mine_clause(self):
        principal = self.service.principal
        return or_(
            Ticket.assigned_user_id == principal.user_id,
            and_(
                Ticket.assigned_user_id.is_(None),
                Ticket.assigned_role_id == principal.role_id,
            ),
        )

    def build_list_queryset(self, **kwargs: Any) -> Select:
        query = (
            select(Ticket)
            .where(self._visible_clause())
            .order_by(Ticket.created_at.desc(), Ticket.id)
        )
        if (kwargs.get("filters") or {}).get("mine"):
            query = query.where(self._mine_clause())
        return query

    def apply_list_filters(self, queryset, filters=None, **kwargs):
        plain = {k: v for k, v in (filters or {}).items() if k not in self.CUSTOM_FILTERS}
        return super().apply_list_filters(queryset, filters=plain, **kwargs)

    async def get(self, record_id: Any, filters: dict[str, Any] | None = None) -> Ticket | None:
        return await self._get_one(conditions=[Ticket.id == record_id, self._visible_clause()])

    async def get_with_joins(self, record_id: Any, joins=None, filters=None) -> Ticket | None:
        return await self.get(record_id)

    async def get_locked(self, record_id: Any, fresh: bool = False) -> Ticket | None:
        """Scoped read that locks the row, so two writers on one ticket take turns.

        `fresh` re-reads an instance the session already holds (needed when the lock is
        taken again after a commit: somebody else may have changed the row meanwhile)."""
        query = (
            select(Ticket)
            .where(Ticket.id == record_id, self._visible_clause())
            .with_for_update(of=Ticket)
        )
        if fresh:
            query = query.execution_options(populate_existing=True)
        return (await self.session.execute(query)).scalars().first()

    # --- internal reads (not tied to who is asking) ------------------------------------

    async def unresolved_blockers(self, parent_id: Any) -> list[Ticket]:
        """Children that block closing their parent. Unscoped on purpose: a caller with a
        narrow view must not be able to close an operation with work they cannot see."""
        query = (
            select(Ticket)
            .join(TicketType, TicketType.id == Ticket.type_id)
            .join(TicketTypeStatus, TicketTypeStatus.id == Ticket.status_id)
            .where(
                Ticket.parent_id == parent_id,
                TicketType.blocks_parent_close.is_(True),
                TicketTypeStatus.is_final.is_(False),
            )
            .order_by(Ticket.code)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def children_of(self, parent_id: Any, type_code: str | None = None) -> list[Ticket]:
        query = select(Ticket).where(Ticket.parent_id == parent_id)
        if type_code:
            query = query.join(TicketType, TicketType.id == Ticket.type_id).where(
                TicketType.code == type_code
            )
        return list((await self.session.execute(query.order_by(Ticket.code))).scalars().all())

    async def get_internal(self, record_id: Any) -> Ticket | None:
        return await self.session.get(Ticket, record_id)

    # --- numbering --------------------------------------------------------------------

    async def _highest_in_series(self, series: str) -> int:
        """Highest number already used by tickets of a series (floor for a new counter)."""
        if series == COUNTER_TICKET_SERIES:
            pattern = rf"^{TICKET_CODE_PREFIX}-[0-9]+$"
        elif series.startswith("op:"):
            _, registration, yy = series.split(":", 2)
            pattern = rf"^{registration}-[0-9]+-{yy}$"
        else:
            return 0
        number = cast(func.split_part(Ticket.code, "-", 2), Integer)
        query = select(func.max(number)).where(Ticket.code.op("~")(pattern))
        return int((await self.session.execute(query)).scalar() or 0)

    async def next_number(self, series: str) -> int:
        """Next number of a series. Atomic: concurrent callers never get the same one, and a
        cancelled ticket never frees its number (the counter only moves forward)."""
        bump = (
            update(TicketCounter)
            .where(TicketCounter.series == series)
            .values(last_value=TicketCounter.last_value + 1)
            .returning(TicketCounter.last_value)
        )
        row = (await self.session.execute(bump)).first()
        if row is not None:
            return int(row[0])
        floor = (
            0
            if series.startswith(COUNTER_CONTROL_SERIES)
            else await self._highest_in_series(series)
        )
        insert = pg_insert(TicketCounter).values(series=series, last_value=floor + 1)
        insert = insert.on_conflict_do_update(
            index_elements=[TicketCounter.series],
            set_={"last_value": TicketCounter.last_value + 1},
        ).returning(TicketCounter.last_value)
        return int((await self.session.execute(insert)).scalar_one())

    # --- audit expansion of the payload -----------------------------------------------

    def _expand_payload(self, changes: list[dict[str, Any]], obj: Ticket) -> list[dict[str, Any]]:
        labels = obj.type.field_labels()
        expanded: list[dict[str, Any]] = []
        for change in changes:
            if change["field"] == "payload":
                expanded.extend(
                    diff_values(change.get("from") or {}, change.get("to") or {}, labels)
                )
            else:
                expanded.append(change)
        return expanded

    def _created_changes(self, obj: Ticket) -> list[dict[str, Any]]:
        return self._expand_payload(super()._created_changes(obj), obj)

    def _pending_changes(self, obj: Ticket) -> list[dict[str, Any]]:
        return self._expand_payload(super()._pending_changes(obj), obj)

    async def _emit_created(self, obj: Ticket) -> None:
        label = self.audit_label(obj)
        parent = f" en {obj.parent_code}" if obj.parent_id else ""
        await self._emit_change(
            AuditAction.CREATE,
            obj,
            f"Creó {obj.type.name} {label}{parent}",
            self._created_changes(obj),
        )

    # --- child rows ---------------------------------------------------------------------

    async def upsert_decision(
        self,
        ticket: Ticket,
        step_code: str,
        step_name: str,
        decision: str,
        observation: str | None,
        actor_id: Any,
        actor_name: str,
        signature_url: str | None = None,
    ) -> tuple[TicketDecision | None, TicketDecision]:
        """Register a step decision; repeating a step replaces it (the old one lives on in
        the audit trail). Returns (previous snapshot or None, current)."""
        now = datetime.now(timezone.utc)
        current = next((d for d in ticket.decisions if d.step_code == step_code), None)
        previous = None
        if current is None:
            current = TicketDecision(ticket_id=ticket.id, step_code=step_code)
            self.session.add(current)
        else:
            previous = TicketDecision(
                step_code=current.step_code,
                step_name=current.step_name,
                decision=current.decision,
                observation=current.observation,
                decided_by=current.decided_by,
            )
        current.step_name = step_name
        current.decision = decision
        current.observation = observation
        current.decided_by_id = actor_id
        current.decided_by = actor_name
        current.decided_at = now
        # The signer's image is copied, not referenced: it keeps showing what was signed
        # even if the user later changes their signature. Only `done` carries one.
        current.signed = signature_url is not None
        current.signature_url = signature_url
        ticket.updated_at = now
        await self.session.flush()
        await self._reload(ticket)
        return previous, current

    async def add_comment(
        self, ticket: Ticket, author_id: Any, author: str, body: str
    ) -> TicketComment:
        comment = TicketComment(ticket_id=ticket.id, author_id=author_id, author=author, body=body)
        self.session.add(comment)
        ticket.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self._reload(ticket)
        return comment

    async def touch(self, ticket: Ticket, **values: Any) -> Ticket:
        """Set columns on a ticket outside the audited update (derived/system state)."""
        for key, value in values.items():
            setattr(ticket, key, value)
        ticket.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self._reload(ticket)
        return ticket

    async def _reload(self, ticket: Ticket) -> None:
        """Re-read the row and the relationships the response reads. A flush expires the
        derived columns, and lazy loading is not possible once the request is serialising."""
        await self.session.refresh(ticket)
        await self.session.refresh(
            ticket, ["type", "status", "decisions", "comments", "closing_record"]
        )

    # --- audit facts ------------------------------------------------------------------

    async def _fact(
        self,
        action: AuditAction,
        ticket: Ticket,
        summary: str,
        changes: list[dict[str, Any]] | None = None,
    ) -> None:
        await self._emit_change(action, ticket, summary, changes or [])

    async def record_status_change(self, ticket: Ticket, before: str, after: str) -> None:
        await self._fact(
            AuditAction.STATUS_CHANGE,
            ticket,
            f"Cambió el estado de {ticket.code}: {before} → {after}",
            [{"field": "status_code", "label": "Estado", "from": before, "to": after}],
        )

    async def record_decision(
        self,
        ticket: Ticket,
        step_name: str,
        previous: TicketDecision | None,
        current: TicketDecision,
    ) -> None:
        verb = "Rectificó" if previous else "Registró"
        changes = [
            {
                "field": "decision",
                "label": step_name,
                "from": DECISION_LABELS.get(previous.decision) if previous else None,
                "to": DECISION_LABELS.get(current.decision),
            }
        ]
        if current.observation or (previous and previous.observation):
            changes.append(
                {
                    "field": "observation",
                    "label": "Observación",
                    "from": previous.observation if previous else None,
                    "to": current.observation,
                }
            )
        await self._fact(
            AuditAction.DECISION,
            ticket,
            f'{verb} el paso "{step_name}" de {ticket.code}',
            changes,
        )

    async def record_comment(self, ticket: Ticket, body: str) -> None:
        await self._fact(AuditAction.COMMENT, ticket, f'Comentó en {ticket.code}: "{body[:80]}"')

    async def record_reassignment(
        self, ticket: Ticket, field: str, label: str, before: str | None, after: str | None
    ) -> None:
        await self._fact(
            AuditAction.UPDATE,
            ticket,
            f"Reasignó {ticket.code} a {after or 'sin asignar'}",
            [{"field": field, "label": label, "from": before, "to": after}],
        )

    async def record_rule_hit(self, ticket: Ticket, hit: dict[str, Any]) -> None:
        await self._fact(
            AuditAction.UPDATE,
            ticket,
            f'Se disparó la regla "{hit["rule_name"]}" en {ticket.code}: {hit["message"]}',
        )

    async def record_authorization(self, ticket: Ticket, actor_name: str, summary: str) -> None:
        await self._fact(AuditAction.DECISION, ticket, f"{actor_name} {summary}")

    async def record_validation(
        self, ticket: Ticket, corrections: list[dict[str, Any]], labels: dict[str, str]
    ) -> None:
        summary = (
            f"Validó el comprobante de {ticket.code} corrigiendo {len(corrections)} campo(s)"
            if corrections
            else f"Validó el comprobante de {ticket.code} sin correcciones"
        )
        await self._fact(
            AuditAction.UPDATE,
            ticket,
            summary,
            [
                {
                    "field": c["key"],
                    "label": labels.get(c["key"], c["key"]),
                    "from": c["from"],
                    "to": c["to"],
                }
                for c in corrections
            ],
        )

    async def record_reading(self, ticket: Ticket, count: int, source: str) -> None:
        await self._fact(
            AuditAction.UPDATE,
            ticket,
            f"Registró una lectura de comprobante en {ticket.code} ({count} campo(s), {source})",
        )

    async def record_denied(self, ticket: Ticket, attempt: str) -> None:
        """A refused attempt on a closed ticket. Written in its own transaction: the 409
        aborts the request's, and the attempt is a fact the trail must keep."""
        await self.emit_independent(
            AuditEntry(
                action=AuditAction.DENIED,
                entity=AuditEntity.TICKET,
                entity_id=str(ticket.id),
                entity_label=ticket.code,
                summary=f"Intento de {attempt} {ticket.code}, que está cerrado",
            )
        )

    async def record_denied_period(self, ticket: Ticket, attempt: str, period: str) -> None:
        """A refused attempt on an expense whose accounting period is closed."""
        await self.emit_independent(
            AuditEntry(
                action=AuditAction.DENIED,
                entity=AuditEntity.TICKET,
                entity_id=str(ticket.id),
                entity_label=ticket.code,
                summary=f"Intento de {attempt} {ticket.code}, cuyo período {period} está cerrado",
            )
        )

    async def record_denied_new_expense(self, label: str, attempt: str, period: str) -> None:
        """A refused attempt to book an expense into a closed period (no ticket exists)."""
        await self.emit_independent(
            AuditEntry(
                action=AuditAction.DENIED,
                entity=AuditEntity.TICKET,
                entity_id=label,
                entity_label=label,
                summary=f"Intento de {attempt} el período {period}, que está cerrado",
            )
        )
