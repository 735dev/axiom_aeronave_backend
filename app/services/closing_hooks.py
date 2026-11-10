"""Signed closing, plugged into the tickets module through its extension points.

- `CLOSE_CHECKS`: an operation counts as closed only with all its supports validated and
  the signature of every role the type demands, loaded and stamped on each signing decision.
- `STATUS_CHANGED`: when the operation reaches its final status the seal is written once.
"""

from __future__ import annotations

from app.config.closing import BLOCKERS_SKIPPED_BY_CLOSE_CHECK
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
    """Stamp every signer, signature, moment and totals when an operation closes for good.
    Cancelling or rejecting (a status that does not count) carries no seal."""
    ticket = context.ticket
    status = ticket.status
    if not (ticket.type.is_root and status.is_final and status.counts_as_committed):
        return
    service = context.service
    repository = service.closings
    by_step = {d.step_code: d for d in ticket.decisions}
    signatures: list[dict] = []
    for step in ticket.type.signing_steps:
        decision = by_step.get(step.code)
        if decision is None or decision.decision != DecisionValue.DONE.value or not decision.signed:
            return
        image = await repository.decision_signature(ticket.id, step.code)
        if not image:
            return
        signatures.append(
            {
                "step_code": step.code,
                "step_name": step.name,
                "user_id": decision.decided_by_id,
                "user_name": decision.decided_by,
                "role_code": getattr(step, "role_code", None),
                "role_name": getattr(step, "role_name", None) or "",
                "signature_url": image,
                "signed_at": decision.decided_at,
            }
        )
    if not signatures:
        return
    # The seal's own signer is the last one to sign: the act that completed the closing.
    last = max(signatures, key=lambda item: item["signed_at"])
    members = [ticket, *await repository.children_of(ticket.id)]
    totals = await repository.totals({"parent_id": ticket.id}, scoped=False)
    await repository.seal(
        ticket,
        signed_by_id=last["user_id"],
        signed_by=last["user_name"],
        signed_role=last["role_name"],
        signature_url=last["signature_url"],
        signed_at=last["signed_at"],
        totals=TotalsRead.model_validate(totals).model_dump(mode="json"),
        files_count=sum((await service.supports.counts(m.id for m in members)).values()),
        folder=folder_id_for(ticket.code, ticket.created_at.year),
        signatures=signatures,
    )


def register() -> None:
    if closing_check not in CLOSE_CHECKS:
        CLOSE_CHECKS.append(closing_check)
    if seal_closing not in TICKET_HOOKS[TicketEvent.STATUS_CHANGED]:
        TICKET_HOOKS[TicketEvent.STATUS_CHANGED].append(seal_closing)
