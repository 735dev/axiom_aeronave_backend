"""Signed closing: the expedient of an operation, its signer and its seal."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import undefer

from app.models.access import AppUser
from app.models.closing import TicketClosing
from app.models.ticket import Ticket, TicketDecision
from app.repositories.ticket_analytics import TicketAnalyticsRepository


class TicketClosingRepository(TicketAnalyticsRepository):
    async def expedient(
        self, root_id: Any, *, scoped: bool = True
    ) -> tuple[Ticket, list[Ticket]] | None:
        """An operation and its work. Only roots have an expedient: any other id is None.

        `scoped=False` is for the closing act itself: a caller with a narrow view must not
        be able to close an operation with work they cannot see."""
        root = await (self.get(root_id) if scoped else self.get_internal(root_id))
        if root is None or not root.type.is_root:
            return None
        query = select(Ticket).where(Ticket.parent_id == root.id).order_by(Ticket.code)
        if scoped:
            query = query.where(self._visible_clause())
        children = list((await self.session.execute(query)).scalars().all())
        return root, children

    async def has_signature(self, user_id: UUID) -> bool:
        query = select(AppUser.signature_updated_at).where(AppUser.id == user_id)
        return (await self.session.execute(query)).scalar() is not None

    async def decision_signature(self, ticket_id: UUID, step_code: str) -> str | None:
        query = select(TicketDecision.signature_url).where(
            TicketDecision.ticket_id == ticket_id, TicketDecision.step_code == step_code
        )
        return (await self.session.execute(query)).scalar()

    async def stamp_of(self, ticket_id: UUID) -> TicketClosing | None:
        query = (
            select(TicketClosing)
            .options(undefer(TicketClosing.signature_url))
            .where(TicketClosing.ticket_id == ticket_id)
        )
        return (await self.session.execute(query)).scalars().first()

    async def seal(
        self,
        ticket: Ticket,
        *,
        signed_by_id: UUID | None,
        signed_by: str,
        signed_role: str,
        signature_url: str,
        signed_at: datetime,
        totals: dict[str, Any],
        files_count: int,
        folder: str,
    ) -> TicketClosing:
        """Write the seal once. A second one for the same ticket is a no-op: the stamp is
        immutable and the first signature is the one that counts."""
        existing = await self.stamp_of(ticket.id)
        if existing is not None:
            return existing
        stamp = TicketClosing(
            ticket_id=ticket.id,
            signed_by_id=signed_by_id,
            signed_by=signed_by,
            signed_role=signed_role,
            signature_url=signature_url,
            signed_at=signed_at,
            totals=totals,
            files_count=files_count,
            folder=folder,
        )
        self.session.add(stamp)
        await self.session.flush()
        await self.session.refresh(ticket, ["closing_record"])
        return stamp
