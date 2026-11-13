"""Wires the notices to the ticket events (see `ticket_hooks.TICKET_HOOKS`).

Imported by `app.services.dependency`, which every endpoint module goes through, so the
hooks exist before the first request. Each hook runs inside the request transaction: if the
request fails, its notices disappear with it. The one exception is a refused close attempt,
whose notice is written independently (see `NotificationService.on_close_blocked`).
"""

from __future__ import annotations

from app.services.notification_service import NotificationService
from app.services.ticket_hooks import TICKET_HOOKS, TicketContext, TicketEvent


def _notifier(context: TicketContext) -> NotificationService:
    service = context.service
    return NotificationService(service.notifications, request=service.request, roles=service.roles)


async def notify_created(context: TicketContext) -> None:
    # The empty technical report opened with an operation is not a finding and must not
    # be announced as one. The pilot is included in the operation notice instead.
    if context.extra.get("system"):
        return
    await _notifier(context).on_ticket_created(context.ticket)


async def notify_status_changed(context: TicketContext) -> None:
    await _notifier(context).on_status_changed(context.ticket)


async def notify_close_blocked(context: TicketContext) -> None:
    await _notifier(context).on_close_blocked(context.ticket, context.extra["blockers"])


async def notify_attachment(context: TicketContext) -> None:
    reading = context.extra.get("reading")
    if reading is not None and reading.status == "extracted":
        await _notifier(context).on_receipt_to_validate(context.ticket, context.extra["file_name"])


def register() -> None:
    """Idempotent: importing twice never doubles a notice."""
    wiring = {
        TicketEvent.CREATED: notify_created,
        TicketEvent.STATUS_CHANGED: notify_status_changed,
        TicketEvent.CLOSE_BLOCKED: notify_close_blocked,
        TicketEvent.ATTACHMENT_ADDED: notify_attachment,
    }
    for event, hook in wiring.items():
        if hook not in TICKET_HOOKS[event]:
            TICKET_HOOKS[event].append(hook)


register()
