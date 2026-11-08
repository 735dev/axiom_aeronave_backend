"""Readings of receipts waiting for a person to validate them."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.models.ticket import TicketExtraction
from app.repositories.base import ProjectRepository


class TicketExtractionRepository(ProjectRepository[TicketExtraction]):
    model = TicketExtraction

    async def for_ticket(self, ticket_id: Any) -> list[TicketExtraction]:
        query = (
            select(TicketExtraction)
            .where(TicketExtraction.ticket_id == ticket_id)
            .order_by(TicketExtraction.created_at, TicketExtraction.id)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def get_of_ticket(self, ticket_id: Any, extraction_id: Any) -> TicketExtraction | None:
        query = select(TicketExtraction).where(
            TicketExtraction.id == extraction_id, TicketExtraction.ticket_id == ticket_id
        )
        return (await self.session.execute(query.with_for_update())).scalars().first()

    async def mark_validated(
        self,
        extraction: TicketExtraction,
        fields: list[dict[str, Any]],
        corrections: list[dict[str, Any]],
        validator: str,
    ) -> TicketExtraction:
        extraction.status = "validated"
        extraction.fields = fields
        extraction.corrections = corrections
        extraction.validated_by = validator
        extraction.validated_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(extraction)
        return extraction

    async def has_pending_doubt(self, ticket_id: Any) -> bool:
        """True when a reading still waits for validation with low-confidence data."""
        for reading in await self.for_ticket(ticket_id):
            if reading.status == "extracted" and any(
                f.get("confidence") == "low" for f in reading.fields or []
            ):
                return True
        return False

    async def discard_unvalidated_of_attachment(self, attachment_id: Any) -> int:
        """A removed file takes its pending reading with it; a validated one stays as history."""
        result = await self.session.execute(
            delete(TicketExtraction).where(
                TicketExtraction.attachment_id == attachment_id,
                TicketExtraction.status != "validated",
            )
        )
        return int(result.rowcount or 0)

    async def of_attachment(self, attachment_id: Any) -> TicketExtraction | None:
        query = select(TicketExtraction).where(TicketExtraction.attachment_id == attachment_id)
        return (await self.session.execute(query)).scalars().first()
