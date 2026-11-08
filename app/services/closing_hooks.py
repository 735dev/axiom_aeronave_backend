"""Signed closing, plugged into the tickets module through its extension points.

- `CLOSE_CHECKS`: an operation counts as closed only with all its supports validated and
  the signature of whoever has to sign it, loaded and stamped on the signature decision.
- `STATUS_CHANGED`: when the operation reaches its final status the seal is written once.
"""

from __future__ import annotations

from app.config.closing import (
    BLOCKERS_SKIPPED_BY_CLOSE_CHECK,
    SIGNATURE_STEP_CODE,
)
from app.config.tickets import DecisionValue
from app.integrations.blackbox.naming import folder_id_for
from app.schemas.totals import TotalsRead
from app.services.ticket_hooks import CLOSE_CHECKS, TICKET_HOOKS, TicketContext, TicketEvent
from app.utils import closing as rules


async def closing_check(context: TicketContext) -> str | None:
    """Refuse the closing of an operation that is not ready to be sealed (422)."""
    if not context.ticket_type.is_root:
        return None
    service = context.service
    # Unscoped on purpose: whoever closes must answer for the whole expedient.
    expedient = await service.closings.expedient(context.ticket.id, scoped=False)
    if expedient is None:
        return None
    root, children = expedient
    members = [root, *children]
    support_counts = await service.supports.counts(m.id for m in members)
    inventory = await service.supports.inventory(members)
    found = rules.blockers(
        root,
        children,
        support_counts,
        [(i.ticket_id, i.ticket_code, i.name) for i in inventory if i.awaiting_validation],
    )
    return rules.hard_block_message(found, BLOCKERS_SKIPPED_BY_CLOSE_CHECK) or (
        rules.signature_problem(root)
    )


async def seal_closing(context: TicketContext) -> None:
    """Stamp signer, signature, moment and totals when an operation closes for good.
    Cancelling or rejecting (a status that does not count) carries no seal."""
    ticket = context.ticket
    status = ticket.status
    if not (ticket.type.is_root and status.is_final and status.counts_as_committed):
        return
    decision = next(
        (
            d
            for d in ticket.decisions
            if d.step_code == SIGNATURE_STEP_CODE and d.decision == DecisionValue.DONE.value
        ),
        None,
    )
    if decision is None or not decision.signed:
        return
    service = context.service
    repository = service.closings
    image = await repository.decision_signature(ticket.id, SIGNATURE_STEP_CODE)
    if not image:
        return
    members = [ticket, *await repository.children_of(ticket.id)]
    totals = await repository.totals({"parent_id": ticket.id}, scoped=False)
    step = ticket.type.step_by_code(SIGNATURE_STEP_CODE)
    await repository.seal(
        ticket,
        signed_by_id=decision.decided_by_id,
        signed_by=decision.decided_by,
        signed_role=getattr(step, "role_name", None) or "",
        signature_url=image,
        signed_at=decision.decided_at,
        totals=TotalsRead.model_validate(totals).model_dump(mode="json"),
        files_count=sum((await service.supports.counts(m.id for m in members)).values()),
        folder=folder_id_for(ticket.code, ticket.created_at.year),
    )


def register() -> None:
    if closing_check not in CLOSE_CHECKS:
        CLOSE_CHECKS.append(closing_check)
    if seal_closing not in TICKET_HOOKS[TicketEvent.STATUS_CHANGED]:
        TICKET_HOOKS[TicketEvent.STATUS_CHANGED].append(seal_closing)
