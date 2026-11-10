"""Rules of the signed closing over an expedient (an operation and its work). Pure: the
repository loads the tickets, nothing here reads the database."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.config.closing import (
    CLOSE_AWAITING_MESSAGE,
    CLOSE_MISSING_SIGNATURES,
    CLOSE_NO_SUPPORT_MESSAGE,
    CLOSE_UNSIGNED_IMAGE,
    NO_RESPONSIBLE_ROLE,
    NO_ROLE_LABEL,
    BlockerKind,
)
from app.config.tickets import DecisionValue
from app.models.ticket import Ticket


@dataclass
class Blocker:
    kind: BlockerKind
    ticket_id: UUID
    ticket_code: str
    detail: str | None = None
    step_code: str | None = None
    role_name: str | None = None


@dataclass
class RoleProgress:
    role_id: UUID | None
    role_name: str
    done: int = 0
    total: int = 0
    pending: list[str] = field(default_factory=list)


def counts_as_committed(ticket: Ticket) -> bool:
    return ticket.status.counts_as_committed


def role_progress(members: list[Ticket]) -> list[RoleProgress]:
    """Steps of every member grouped by the responsible role. Any registered decision counts
    as done: it is the same criterion with which the closing demands the required steps."""
    groups: dict[Any, RoleProgress] = {}
    for ticket in members:
        done = {d.step_code for d in ticket.decisions}
        for step in ticket.type.steps:
            group = groups.setdefault(
                step.role_id,
                RoleProgress(step.role_id, getattr(step, "role_name", None) or NO_RESPONSIBLE_ROLE),
            )
            group.total += 1
            if step.code in done:
                group.done += 1
            else:
                group.pending.append(f"{ticket.code} · {step.name}")
    return list(groups.values())


def needs_support(ticket: Ticket) -> bool:
    """An expense-like child: it carries money and its type has a file field."""
    return ticket.amount is not None and any(f.type == "file" for f in ticket.type.fields)


def blockers(
    root: Ticket,
    children: list[Ticket],
    support_counts: dict[UUID, int],
    awaiting: list[tuple[UUID, str, str]],
) -> list[Blocker]:
    """What keeps the expedient from closing. The screen and the closing act share this
    list: if they diverged the screen would say "ready" and the server would say no.

    `awaiting` holds (ticket_id, ticket_code, name) of readings nobody validated yet."""
    found: list[Blocker] = []
    done = {d.step_code for d in root.decisions}
    signed = signed_step_codes(root)
    for step in root.type.steps:
        if step.requires_signature:
            if step.code not in signed:
                found.append(
                    Blocker(
                        BlockerKind.SIGNATURE,
                        root.id,
                        root.code,
                        step.name,
                        step_code=step.code,
                        role_name=role_label(step),
                    )
                )
        elif step.required and step.code not in done:
            found.append(
                Blocker(
                    BlockerKind.PENDING_STEP, root.id, root.code, step.name, step_code=step.code
                )
            )
    for child in children:
        if not child.status.is_final and child.type.blocks_parent_close:
            found.append(Blocker(BlockerKind.UNRESOLVED_CHILD, child.id, child.code))
    for child in children:
        if (
            needs_support(child)
            and counts_as_committed(child)
            and support_counts.get(child.id, 0) == 0
        ):
            found.append(Blocker(BlockerKind.EXPENSE_WITHOUT_SUPPORT, child.id, child.code))
    for ticket_id, code, name in awaiting:
        found.append(Blocker(BlockerKind.RECEIPT_AWAITING_VALIDATION, ticket_id, code, name))
    return found


def is_ready(found: list[Blocker]) -> bool:
    """Ready to sign: only signatures are missing (signing is the act that closes)."""
    return all(b.kind == BlockerKind.SIGNATURE for b in found)


def hard_block_message(found: list[Blocker], skip: frozenset[BlockerKind]) -> str | None:
    """Text for the 422 naming the offending tickets, or None when nothing blocks."""
    hard = [b for b in found if b.kind not in skip]
    no_support = [b for b in hard if b.kind == BlockerKind.EXPENSE_WITHOUT_SUPPORT]
    if no_support:
        return CLOSE_NO_SUPPORT_MESSAGE.format(codes=", ".join(b.ticket_code for b in no_support))
    awaiting = [b for b in hard if b.kind == BlockerKind.RECEIPT_AWAITING_VALIDATION]
    if awaiting:
        codes = ", ".join(dict.fromkeys(b.ticket_code for b in awaiting))
        return CLOSE_AWAITING_MESSAGE.format(codes=codes)
    return None


def role_label(step: Any) -> str:
    return getattr(step, "role_name", None) or NO_ROLE_LABEL


def signed_step_codes(root: Ticket) -> set[str]:
    """Signing steps with a fulfilled, signed decision."""
    wanted = {s.code for s in root.type.signing_steps}
    return {
        d.step_code
        for d in root.decisions
        if d.step_code in wanted and d.decision == DecisionValue.DONE.value and d.signed
    }


def signature_problem(root: Ticket) -> str | None:
    """Why the signatures cannot seal the closing, or None. Types without signing steps
    close without one."""
    steps = root.type.signing_steps
    if not steps:
        return None
    by_step = {d.step_code: d for d in root.decisions}
    missing: list[str] = []
    unsigned: list[str] = []
    for step in steps:
        decision = by_step.get(step.code)
        if decision is None or decision.decision != DecisionValue.DONE.value:
            missing.append(role_label(step))
        elif not decision.signed:
            unsigned.append(role_label(step))
    if missing:
        return CLOSE_MISSING_SIGNATURES.format(roles=", ".join(dict.fromkeys(missing)))
    if unsigned:
        return CLOSE_UNSIGNED_IMAGE.format(roles=", ".join(dict.fromkeys(unsigned)))
    return None
