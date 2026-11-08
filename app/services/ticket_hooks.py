"""Extension points of the tickets module.

Other modules (attachments, notifications, closing and signature, reconciliation) plug in
here instead of editing `TicketService`:

- `TICKET_HOOKS[event]`: async callables run inside the request transaction right after the
  event. An exception aborts the request and rolls everything back, so a hook that must not
  break the flow has to handle its own errors.
- `CLOSE_CHECKS`: async callables asked before a ticket moves to a final state that counts
  as committed. Return a Spanish message to refuse the move (422), or None to allow it.
  The signed-closing module registers its supports/signature check here.

Register at import time of the module that owns the behaviour.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.ticket import Ticket, TicketType
    from app.services.ticket_service import TicketService


class TicketEvent(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    STATUS_CHANGED = "status_changed"
    DECISION = "decision"
    AUTHORIZED = "authorized"
    READING_VALIDATED = "reading_validated"
    CLOSE_BLOCKED = "close_blocked"
    ATTACHMENT_ADDED = "attachment_added"


@dataclass
class TicketContext:
    service: "TicketService"
    ticket: "Ticket"
    ticket_type: "TicketType"
    event: TicketEvent
    #: Event specific data: `previous_status` (status_changed), `target_status` (close checks),
    #: `system` (created: True for tickets the system opens itself), `blockers` (close_blocked),
    #: `reading` (attachment_added: the stored reading, if any).
    extra: dict[str, Any]


TicketHook = Callable[[TicketContext], Awaitable[None]]
CloseCheck = Callable[[TicketContext], Awaitable[str | None]]

TICKET_HOOKS: dict[TicketEvent, list[TicketHook]] = {event: [] for event in TicketEvent}
CLOSE_CHECKS: list[CloseCheck] = []
