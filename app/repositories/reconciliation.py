"""Reconciliation: the facts of a month, the justifications and the period close."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config.audit import AuditAction, AuditEntity
from app.config.tickets import ExtractionStatus, FieldRole
from app.models.attachment import TicketAttachment
from app.models.reconciliation import ReconciliationAcknowledgement, ReconciliationPeriod
from app.models.ticket import Ticket
from app.repositories.audit_trail import AuditEntry
from app.repositories.base import ProjectRepository
from app.repositories.ticket_analytics import TicketAnalyticsRepository
from app.repositories.ticket_support import TicketSupportRepository
from app.utils.principal import Principal
from app.utils.reconciliation import Acknowledgement, Expense, Receipt, period_bounds

PERIOD_LOCK_NAMESPACE = "reconciliation:"


class ReconciliationPeriodRepository(ProjectRepository[ReconciliationPeriod]):
    """Closed periods. Tickets read through here before any write that touches an expense."""

    model = ReconciliationPeriod
    default_ordering = ("-period",)

    async def is_closed(self, period: str, *, lock: bool = False) -> bool:
        """With `lock`, takes a shared advisory lock on the period until the transaction
        ends: a close (exclusive lock) waits for writers in flight, and a writer that starts
        after the close sees it. Without it a close and an edit could cross each other."""
        if lock:
            await self.session.execute(
                select(
                    func.pg_advisory_xact_lock_shared(func.hashtext(PERIOD_LOCK_NAMESPACE + period))
                )
            )
        query = select(ReconciliationPeriod.id).where(ReconciliationPeriod.period == period)
        return (await self.session.execute(query)).first() is not None

    async def get_period(self, period: str) -> ReconciliationPeriod | None:
        query = select(ReconciliationPeriod).where(ReconciliationPeriod.period == period)
        return (await self.session.execute(query)).scalars().first()

    async def all_closed(self) -> list[ReconciliationPeriod]:
        return list((await self.session.execute(self.build_list_queryset())).scalars().all())


class ReconciliationRepository(TicketAnalyticsRepository):
    # --- facts of a month ---------------------------------------------------------------

    async def expenses_around(self, period: str, *, scoped: bool = True) -> list[Expense]:
        """Expenses of the month plus the committed ones of the same operations (any month):
        a receipt loaded on an operation can match an expense booked in another month."""
        start, end = period_bounds(period)
        visible = [self._visible_clause()] if scoped else []
        in_period = select(Ticket.id, Ticket.parent_id).where(
            Ticket.amount.is_not(None), Ticket.accounting_date.between(start, end), *visible
        )
        rows = (await self.session.execute(in_period)).all()
        parents = {parent for _, parent in rows if parent is not None}
        ids = {ticket_id for ticket_id, _ in rows}
        query = (
            select(Ticket)
            .where(
                Ticket.amount.is_not(None),
                or_(
                    Ticket.id.in_(ids) if ids else Ticket.id.is_(None),
                    Ticket.parent_id.in_(parents) if parents else Ticket.id.is_(None),
                ),
                *visible,
            )
            .order_by(Ticket.accounting_date, Ticket.code)
        )
        tickets = list((await self.session.execute(query)).scalars().all())
        return await self._expenses(tickets)

    async def _expenses(self, tickets: list[Ticket]) -> list[Expense]:
        files: dict[Any, list[TicketAttachment]] = {}
        for file in await TicketSupportRepository(self.session).files(t.id for t in tickets):
            files.setdefault(file.ticket_id, []).append(file)

        labels = await self.concept_labels(tickets)

        parent_codes = {t.id: t.code for t in tickets}
        expenses: list[Expense] = []
        for ticket in tickets:
            mine = files.get(ticket.id, [])
            expenses.append(
                Expense(
                    ticket_id=str(ticket.id),
                    code=ticket.code,
                    title=ticket.title,
                    concept=self._concept(ticket, labels),
                    operation_id=str(ticket.parent_id) if ticket.parent_id else None,
                    operation_code=ticket.parent_code or parent_codes.get(ticket.parent_id, ""),
                    accounting_date=ticket.accounting_date,
                    status_name=ticket.status.name,
                    committed=ticket.status.counts_as_committed,
                    amount=ticket.amount,
                    currency=ticket.currency or "",
                    amount_base=ticket.amount_base or Decimal(0),
                    base_currency=ticket.base_currency or "",
                    supports=len(mine),
                    has_reading=any(f.extraction is not None for f in mine),
                    receipts=self._receipts(ticket, mine),
                )
            )
        return expenses

    @staticmethod
    def _concept(ticket: Ticket, labels: dict[Any, str]) -> str:
        item = ticket.type.field_by_role(FieldRole.ITEM.value)
        if item is None or (ticket.payload or {}).get(item.key) in (None, ""):
            return ""
        return labels[ticket.id]

    @staticmethod
    def _receipts(ticket: Ticket, files: list[TicketAttachment]) -> list[Receipt]:
        """Amount read from VALIDATED readings only: until a person answers for it, a
        reading is a proposal, not support."""
        amount_field = ticket.type.field_by_role(FieldRole.AMOUNT.value)
        currency_field = ticket.type.field_by_role(FieldRole.CURRENCY.value)
        if amount_field is None:
            return []
        receipts: list[Receipt] = []
        for file in files:
            reading = file.extraction
            if reading is None or reading.status != ExtractionStatus.VALIDATED.value:
                continue
            values = {f["key"]: f["value"] for f in reading.fields or []}
            raw = values.get(amount_field.key)
            if raw in (None, ""):
                continue
            try:
                amount = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                continue
            if not amount.is_finite():
                continue
            currency = values.get(currency_field.key) if currency_field else None
            receipts.append(
                Receipt(
                    file_id=str(file.id),
                    file_name=file.name,
                    amount=amount,
                    currency=str(currency or ticket.currency or ""),
                )
            )
        return receipts

    # --- justifications -----------------------------------------------------------------

    async def acknowledgements(self, period: str) -> dict[str, Acknowledgement]:
        query = select(ReconciliationAcknowledgement).where(
            ReconciliationAcknowledgement.period == period
        )
        return {
            row.key: Acknowledgement(
                note=row.note,
                actor_id=str(row.actor_id) if row.actor_id else None,
                actor_name=row.actor_name,
                actor_role=row.actor_role,
                at=row.at,
            )
            for row in (await self.session.execute(query)).scalars().all()
        }

    async def acknowledge(
        self, period: str, key: str, note: str, actor: Principal, summary: str
    ) -> None:
        """Record (or replace) the justification of one difference, and the fact."""
        insert = pg_insert(ReconciliationAcknowledgement).values(
            period=period,
            key=key,
            note=note,
            actor_id=actor.user_id,
            actor_name=actor.full_name,
            actor_role=actor.role_name,
            at=datetime.now(timezone.utc),
        )
        await self.session.execute(
            insert.on_conflict_do_update(
                constraint="uq_reconciliation_ack_period_key",
                set_={
                    "note": insert.excluded.note,
                    "actor_id": insert.excluded.actor_id,
                    "actor_name": insert.excluded.actor_name,
                    "actor_role": insert.excluded.actor_role,
                    "at": insert.excluded.at,
                },
            )
        )
        await self._reconciliation_fact(
            AuditAction.UPDATE,
            period,
            summary,
            [{"field": "justification", "label": "Justificación", "from": None, "to": note}],
        )

    # --- close --------------------------------------------------------------------------

    async def lock_for_close(self, period: str) -> None:
        """Exclusive lock on the period until the transaction ends (see `is_closed`)."""
        await self.session.execute(
            select(func.pg_advisory_xact_lock(func.hashtext(PERIOD_LOCK_NAMESPACE + period)))
        )

    async def close(
        self,
        period: str,
        actor: Principal,
        rows_count: int,
        total_base: Decimal,
        acknowledged_count: int,
        summary: str,
    ) -> ReconciliationPeriod | None:
        """Insert the closed period; None when it already was (a double or concurrent close
        never closes twice)."""
        insert = (
            pg_insert(ReconciliationPeriod)
            .values(
                period=period,
                closed_at=datetime.now(timezone.utc),
                closed_by_id=actor.user_id,
                closed_by_name=actor.full_name,
                closed_by_role=actor.role_name,
                rows_count=rows_count,
                total_base=total_base,
                acknowledged_count=acknowledged_count,
            )
            .on_conflict_do_nothing(index_elements=[ReconciliationPeriod.period])
            .returning(ReconciliationPeriod.id)
        )
        created = (await self.session.execute(insert)).first()
        if created is None:
            return None
        await self._reconciliation_fact(
            AuditAction.UPDATE,
            period,
            summary,
            [{"field": "period_status", "label": "Período", "from": "abierto", "to": "cerrado"}],
        )
        return await self.session.get(ReconciliationPeriod, created[0])

    async def record_export(self, period: str, rows: int) -> None:
        await self._reconciliation_fact(
            AuditAction.EXPORT,
            period,
            f"Exportó el detalle conciliado de {period} ({rows} línea(s))",
            [{"field": "rows", "label": "Líneas exportadas", "from": None, "to": rows}],
        )

    async def _reconciliation_fact(
        self, action: AuditAction, period: str, summary: str, changes: list[dict[str, Any]]
    ) -> None:
        await self.emit(
            AuditEntry(
                action=action,
                entity=AuditEntity.RECONCILIATION,
                entity_id=period,
                entity_label=f"Conciliación {period}",
                summary=summary,
                changes=changes,
            )
        )
