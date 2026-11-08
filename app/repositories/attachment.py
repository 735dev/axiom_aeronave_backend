"""Attachments of tickets: scoped reads, sequence numbering and the audit facts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, false, func, nulls_last, select, true
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from app.config.audit import AuditAction, AuditEntity
from app.models.attachment import AttachmentUpload, TicketAttachment
from app.models.ticket import Ticket, TicketExtraction, TicketType
from app.repositories.audit_trail import AuditEntry
from app.repositories.base import AuditedRepository
from app.utils.ticket_access import viewable_type_codes


class AttachmentRepository(AuditedRepository[TicketAttachment]):
    model = TicketAttachment
    audit_entity = AuditEntity.TICKET
    #: Facts about attachments are written explicitly (they belong to the ticket's timeline).
    audit_labels: dict[str, str] = {}

    #: Filters this repository resolves itself (they are not columns of the model).
    CUSTOM_FILTERS = frozenset()

    def audit_label(self, obj: TicketAttachment) -> str:
        return obj.filename

    async def _emit_created(self, obj: TicketAttachment) -> None:
        """Replaced by `record_attached`, which knows the ticket."""

    async def _emit_updated(self, obj: TicketAttachment, changes: list[dict[str, Any]]) -> None:
        """Replaced by `record_removed`."""

    # --- row-level scope: the tickets the caller may view ---------------------------------

    def _visible_tickets(self):
        codes = viewable_type_codes(self.service.principal.permissions)
        if codes is None:
            return true()
        if not codes:
            return false()
        return Ticket.type_id.in_(select(TicketType.id).where(TicketType.code.in_(sorted(codes))))

    def build_list_queryset(self, **kwargs: Any) -> Select:
        """The validation queue: live attachments whose reading still waits for a person,
        of viewable tickets only. Doubtful readings first, then the larger amounts."""
        doubtful = TicketExtraction.fields.contains([{"confidence": "low"}])
        return (
            select(TicketAttachment)
            .join(Ticket, Ticket.id == TicketAttachment.ticket_id)
            .join(TicketExtraction, TicketExtraction.attachment_id == TicketAttachment.id)
            .where(
                TicketAttachment.deleted_at.is_(None),
                TicketExtraction.status == "extracted",
                self._visible_tickets(),
            )
            .options(selectinload(TicketAttachment.ticket))
            .order_by(
                doubtful.desc(),
                nulls_last(Ticket.amount_base.desc()),
                TicketAttachment.uploaded_at.desc(),
                TicketAttachment.id,
            )
        )

    # --- reads ----------------------------------------------------------------------------

    async def live_of_ticket(self, ticket_id: Any) -> list[TicketAttachment]:
        query = (
            select(TicketAttachment)
            .where(TicketAttachment.ticket_id == ticket_id, TicketAttachment.deleted_at.is_(None))
            .order_by(TicketAttachment.uploaded_at, TicketAttachment.id)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def get_of_ticket(self, ticket_id: Any, attachment_id: Any) -> TicketAttachment | None:
        """A live attachment of THAT ticket; any other combination is 'not found'."""
        query = select(TicketAttachment).where(
            TicketAttachment.id == attachment_id,
            TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.deleted_at.is_(None),
        )
        return (await self.session.execute(query)).scalars().first()

    async def find_by_key(
        self, ticket_id: Any, uploader_id: Any, key: str
    ) -> TicketAttachment | None:
        query = select(TicketAttachment).where(
            TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.uploaded_by_id == uploader_id,
            TicketAttachment.idempotency_key == key,
            TicketAttachment.deleted_at.is_(None),
        )
        return (await self.session.execute(query)).scalars().first()

    async def next_sequence(self, ticket_id: Any, document_type: str) -> int:
        """Next ordinal for (ticket, document type). Removed files keep their number, and so do
        slots reserved by an upload still in flight: an archived name is never handed out twice."""
        linked = select(func.coalesce(func.max(TicketAttachment.sequence), 0)).where(
            TicketAttachment.ticket_id == ticket_id,
            TicketAttachment.document_type == document_type,
        )
        reserved = select(func.coalesce(func.max(AttachmentUpload.sequence), 0)).where(
            AttachmentUpload.ticket_id == ticket_id,
            AttachmentUpload.document_type == document_type,
        )
        highest = max(
            int((await self.session.execute(linked)).scalar_one()),
            int((await self.session.execute(reserved)).scalar_one()),
        )
        return highest + 1

    # --- upload slots (the external call runs between reserving and linking) ---------------

    async def find_slot(
        self, ticket_id: Any, uploader_id: Any, key: str
    ) -> AttachmentUpload | None:
        query = select(AttachmentUpload).where(
            AttachmentUpload.ticket_id == ticket_id,
            AttachmentUpload.uploader_id == uploader_id,
            AttachmentUpload.idempotency_key == key,
        )
        return (await self.session.execute(query)).scalars().first()

    async def slots_of_ticket(self, ticket_id: Any) -> int:
        query = (
            select(func.count())
            .select_from(AttachmentUpload)
            .where(AttachmentUpload.ticket_id == ticket_id)
        )
        return int((await self.session.execute(query)).scalar_one())

    async def reserve_slot(self, values: dict[str, Any]) -> AttachmentUpload:
        slot = AttachmentUpload(**values)
        self.session.add(slot)
        await self.session.flush()
        return slot

    async def checkpoint(self) -> None:
        """Commit what the request has done so far, releasing the ticket row lock.

        The only mid-request commit of the code base, used by the upload so the call to
        Blackbox does not hold the lock. Objects stay usable (expire_on_commit is off)."""
        await self.session.commit()

    async def release_slot(self, slot_id: Any) -> None:
        """Forget a slot in a committed step of its own (compensation after a failure)."""
        await self.session.execute(delete(AttachmentUpload).where(AttachmentUpload.id == slot_id))
        await self.session.commit()

    async def drop_slot(self, slot_id: Any) -> None:
        """Forget a slot inside the current transaction (it is linked, or no longer needed)."""
        await self.session.execute(delete(AttachmentUpload).where(AttachmentUpload.id == slot_id))

    async def purge_stale_slots(self, cutoff: Any) -> int:
        result = await self.session.execute(
            delete(AttachmentUpload).where(AttachmentUpload.created_at < cutoff)
        )
        return int(result.rowcount or 0)

    # --- writes ---------------------------------------------------------------------------

    async def add(self, ticket: Ticket, values: dict[str, Any]) -> TicketAttachment:
        created = await self.create({**values, "ticket_id": ticket.id})
        await self.session.refresh(created, ["extraction"])
        await self.session.refresh(ticket, ["attachments"])
        return created

    async def reload_extraction(self, attachment: TicketAttachment) -> None:
        await self.session.refresh(attachment, ["extraction"])

    async def mark_removed(
        self, ticket: Ticket, attachment: TicketAttachment, actor_name: str
    ) -> None:
        attachment.deleted_at = datetime.now(timezone.utc)
        attachment.deleted_by = actor_name
        await self.session.flush()
        await self.session.refresh(ticket, ["attachments"])

    # --- audit facts (entity: the ticket, so they show in its timeline) ---------------------

    async def record_attached(self, ticket: Ticket, name: str, reading: str | None) -> None:
        changes = (
            [{"field": "extraction", "label": "Lectura automática", "from": None, "to": reading}]
            if reading
            else []
        )
        await self.emit(
            AuditEntry(
                action=AuditAction.ATTACH,
                entity=AuditEntity.TICKET,
                entity_id=str(ticket.id),
                entity_label=ticket.code,
                summary=f'Adjuntó "{name}" a {ticket.code}',
                changes=changes,
            )
        )

    async def record_removed(self, ticket: Ticket, attachment: TicketAttachment) -> None:
        await self.emit(
            AuditEntry(
                action=AuditAction.DELETE,
                entity=AuditEntity.TICKET,
                entity_id=str(ticket.id),
                entity_label=ticket.code,
                summary=f'Quitó el archivo "{attachment.name}" de {ticket.code}',
                changes=[
                    {
                        "field": "attachment",
                        "label": "Archivo",
                        "from": attachment.filename,
                        "to": None,
                    }
                ],
            )
        )
