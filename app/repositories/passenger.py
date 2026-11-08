"""Passengers of an operation's manifest, and the search across operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, delete, func, select, update

from app.config.audit import AuditAction, AuditEntity
from app.config.closing import MAX_PASSENGER_RESULTS
from app.models.attachment import TicketAttachment
from app.models.passenger import TicketPassenger
from app.models.ticket import Ticket, TicketType, TicketTypeStatus
from app.repositories.audit_trail import AuditEntry
from app.repositories.ticket import TicketRepository
from app.utils.search import LIKE_ESCAPE, escape_like


@dataclass
class PassengerOperation:
    id: UUID
    code: str
    date: date
    route: str
    status_code: str
    status_name: str
    status_is_final: bool


@dataclass
class PassengerMatch:
    document: str
    full_name: str
    nationality: str | None
    passport: dict[str, UUID] | None = None
    operations: list[PassengerOperation] = field(default_factory=list)


def _route(payload: dict[str, Any]) -> str:
    origin, destination = str(payload.get("origin") or ""), str(payload.get("destination") or "")
    return f"{origin or '?'} → {destination or '?'}" if origin or destination else ""


class TicketPassengerRepository(TicketRepository):
    async def root_type_codes(self) -> set[str]:
        query = select(TicketType.code).where(TicketType.is_root.is_(True))
        return set((await self.session.execute(query)).scalars().all())

    async def manifest(self, ticket_id: Any) -> list[TicketPassenger]:
        query = (
            select(TicketPassenger)
            .where(TicketPassenger.ticket_id == ticket_id)
            .order_by(TicketPassenger.position, TicketPassenger.id)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def replace_manifest(
        self, ticket: Ticket, rows: list[dict[str, Any]]
    ) -> list[TicketPassenger]:
        """Replace the whole manifest in the caller's transaction, and record the change."""
        before = len(await self.manifest(ticket.id))
        await self.session.execute(
            delete(TicketPassenger).where(TicketPassenger.ticket_id == ticket.id)
        )
        created = [
            TicketPassenger(ticket_id=ticket.id, position=index, **row)
            for index, row in enumerate(rows)
        ]
        self.session.add_all(created)
        await self.touch(ticket)
        await self._fact(
            AuditAction.UPDATE,
            ticket,
            f"Actualizó el manifiesto de {ticket.code}",
            [{"field": "passengers", "label": "Pasajeros", "from": before, "to": len(created)}],
        )
        return await self.manifest(ticket.id)

    async def search(
        self,
        name_key: str,
        document_key: str | None,
    ) -> list[PassengerMatch]:
        """Who travelled on which operations. The scope is part of the query: only
        operations of types the caller may read are looked at, and wildcards typed by the
        user are matched literally."""
        match = TicketPassenger.name_key.like(f"%{escape_like(name_key)}%", escape=LIKE_ESCAPE)
        if document_key:
            match = match | TicketPassenger.document_key.like(
                f"%{escape_like(document_key)}%", escape=LIKE_ESCAPE
            )
        scope = [
            self._visible_clause(),
            TicketType.is_root.is_(True),
        ]
        people = (
            select(
                TicketPassenger.document_key,
                func.max(Ticket.accounting_date).label("last_date"),
            )
            .join(Ticket, Ticket.id == TicketPassenger.ticket_id)
            .join(TicketType, TicketType.id == Ticket.type_id)
            .where(match, *scope)
            .group_by(TicketPassenger.document_key)
            .order_by(func.max(Ticket.accounting_date).desc(), TicketPassenger.document_key)
            .limit(MAX_PASSENGER_RESULTS)
        )
        keys = [row[0] for row in (await self.session.execute(people)).all()]
        if not keys:
            return []
        rows = await self.session.execute(
            select(TicketPassenger, Ticket, TicketTypeStatus)
            .join(Ticket, Ticket.id == TicketPassenger.ticket_id)
            .join(TicketType, TicketType.id == Ticket.type_id)
            .join(TicketTypeStatus, TicketTypeStatus.id == Ticket.status_id)
            .where(TicketPassenger.document_key.in_(keys), *scope)
            .order_by(Ticket.accounting_date.desc(), Ticket.code)
        )
        people_by_key: dict[str, PassengerMatch] = {}
        for passenger, ticket, status in rows.all():
            entry = people_by_key.get(passenger.document_key)
            if entry is None:
                # Most recent operation first: it fixes the person's current name.
                entry = PassengerMatch(
                    passenger.document, passenger.full_name, passenger.nationality
                )
                people_by_key[passenger.document_key] = entry
            if entry.passport is None and passenger.passport_ref:
                entry.passport = {"ticket_id": ticket.id, "file_id": passenger.passport_ref}
            entry.operations.append(
                PassengerOperation(
                    ticket.id,
                    ticket.code,
                    ticket.accounting_date,
                    _route(ticket.payload or {}),
                    status.code,
                    status.name,
                    status.is_final,
                )
            )
        return [people_by_key[key] for key in keys if key in people_by_key]

    # --- retention ---------------------------------------------------------------------------

    async def anonymize_expired(self, cutoff: date, retention_days: int) -> tuple[int, int]:
        """Erase the personal data of passengers on closed operations whose accounting date is
        before `cutoff`, and detach their passport files. Returns (passengers, passports).

        The row stays (manifest size, history of the operation) but name, document,
        nationality and passport reference are gone, together with the comparable keys the
        search reads. The original passport image lives in Blackbox, whose API publishes no
        delete: only our reference is removed (see docs/RETENCION.md)."""
        expired = (
            select(Ticket.id)
            .join(TicketType, TicketType.id == Ticket.type_id)
            .join(TicketTypeStatus, TicketTypeStatus.id == Ticket.status_id)
            .where(
                TicketType.is_root.is_(True),
                TicketTypeStatus.is_final.is_(True),
                Ticket.accounting_date < cutoff,
            )
        )
        now = datetime.now(timezone.utc)
        token = func.upper(
            func.substr(func.replace(cast(TicketPassenger.id, String), "-", ""), 1, 12)
        )
        passengers = await self.session.execute(
            update(TicketPassenger)
            .where(TicketPassenger.ticket_id.in_(expired), TicketPassenger.anonymized_at.is_(None))
            .values(
                full_name="Pasajero anonimizado",
                document=func.concat("ANON-", token),
                nationality=None,
                passport_ref=None,
                name_key="pasajero anonimizado",
                document_key=func.concat("ANON-", token),
                anonymized_at=now,
            )
        )
        passports = await self.session.execute(
            update(TicketAttachment)
            .where(
                TicketAttachment.ticket_id.in_(expired),
                TicketAttachment.document_type == "pasaporte",
                TicketAttachment.deleted_at.is_(None),
            )
            .values(deleted_at=now, deleted_by="Retención de datos personales")
        )
        people, files = int(passengers.rowcount or 0), int(passports.rowcount or 0)
        if people or files:
            await self.emit(
                AuditEntry(
                    action=AuditAction.UPDATE,
                    entity=AuditEntity.PASSENGER,
                    entity_id="retention",
                    entity_label="Retención de datos personales",
                    summary=(
                        f"Anonimizó {people} pasajero(s) y retiró {files} pasaporte(s) de "
                        f"operaciones cerradas anteriores a {cutoff:%Y-%m-%d}"
                    ),
                    changes=[
                        {
                            "field": "retention_days",
                            "label": "Retención (días)",
                            "from": None,
                            "to": retention_days,
                        },
                        {"field": "passengers", "label": "Pasajeros", "from": None, "to": people},
                        {"field": "passports", "label": "Pasaportes", "from": None, "to": files},
                    ],
                )
            )
        return people, files
