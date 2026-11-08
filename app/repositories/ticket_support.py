"""Support inventory: the evidence loaded on tickets.

SEAM with the attachments module. Closing, reconciliation and the passport reference read
supports only through this class, which is the single place that knows what a support is
(today: the live `TicketAttachment` rows and the reading each one carries). See
docs/CIERRE_Y_CONCILIACION.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.config.tickets import ExtractionStatus
from app.models.attachment import TicketAttachment
from app.models.ticket import Ticket
from app.repositories.base import ProjectRepository


@dataclass(frozen=True)
class SupportItem:
    ticket_id: UUID
    ticket_code: str
    file_id: UUID
    document_type: str
    name: str
    mime: str
    size: int
    filename: str
    folder: str
    uploaded_by: str
    awaiting_validation: bool


class TicketSupportRepository(ProjectRepository[TicketAttachment]):
    model = TicketAttachment

    async def files(self, ticket_ids: Iterable[Any]) -> list[TicketAttachment]:
        """Live files of the given tickets, oldest first, each with its reading loaded."""
        ids = list(ticket_ids)
        if not ids:
            return []
        query = (
            select(TicketAttachment)
            .where(TicketAttachment.ticket_id.in_(ids), TicketAttachment.deleted_at.is_(None))
            .order_by(TicketAttachment.uploaded_at, TicketAttachment.id)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def inventory(self, tickets: list[Ticket]) -> list[SupportItem]:
        by_id = {t.id: t for t in tickets}
        items: list[SupportItem] = []
        for file in await self.files(by_id):
            reading = file.extraction
            items.append(
                SupportItem(
                    ticket_id=file.ticket_id,
                    ticket_code=by_id[file.ticket_id].code,
                    file_id=file.id,
                    document_type=file.document_type,
                    name=file.name,
                    mime=file.mime,
                    size=file.size,
                    filename=file.filename,
                    folder=file.folder,
                    uploaded_by=file.uploaded_by,
                    awaiting_validation=bool(
                        reading and reading.status == ExtractionStatus.EXTRACTED.value
                    ),
                )
            )
        return items

    async def counts(self, ticket_ids: Iterable[Any]) -> dict[UUID, int]:
        counts: dict[UUID, int] = {}
        for file in await self.files(ticket_ids):
            counts[file.ticket_id] = counts.get(file.ticket_id, 0) + 1
        return counts

    async def belongs_to(self, ticket_id: Any, support_id: Any) -> bool:
        query = select(TicketAttachment.id).where(
            TicketAttachment.id == support_id,
            TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.deleted_at.is_(None),
        )
        return (await self.session.execute(query)).first() is not None
