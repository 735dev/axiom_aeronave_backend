"""Notices: who they are for, what the caller sees, and what each ticket event says.

Audience filtering happens in the repository query, never after the fact: the server does
not load other people's notices to hide them. Reading is personal.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.notifications import (
    BODY_MAX,
    CREW_ROLES,
    NO_OPERATION,
    NO_ROLE,
    ROLE_ADMIN,
    ROLE_MAINTENANCE,
    ROLE_OPERATIONS,
    ROLE_PILOT,
    TITLE_MAX,
    TYPE_INAC,
    TYPE_TECHNICAL_REPORT,
    NotificationKind,
)
from app.models.notification import Notification
from app.models.ticket import Ticket
from app.repositories.notification import NotificationRepository
from app.repositories.role import RoleRepository
from app.services.base import ProjectService

logger = logging.getLogger(__name__)


def _unique(ids: list[UUID | None]) -> list[UUID]:
    seen: list[UUID] = []
    for item in ids:
        if item is not None and item not in seen:
            seen.append(item)
    return seen


class NotificationService(ProjectService[Notification]):
    repository: NotificationRepository
    roles: RoleRepository

    filter_whitelist = frozenset({"kind", "ticket_id", "unread"})

    # ------------------------------------------------------------------ reads (the caller's)

    def get_filters(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        incoming = super().get_filters(filters)
        return {k: v for k, v in incoming.items() if v is not None}

    async def unread_count(self) -> int:
        return await self.repository.unread_count()

    async def set_read(self, id, read: bool) -> Notification:  # noqa: A002
        """Mark (or unmark) a notice for the caller only. Foreign and missing are both 404."""
        notice = await self.repository.get(id)
        if notice is None:
            raise NotFoundException("aviso no encontrado")
        if read:
            await self.repository.mark_read(notice.id)
        else:
            await self.repository.mark_unread(notice.id)
        notice.read = read
        return notice

    async def mark_all_read(self) -> tuple[int, int]:
        marked = await self.repository.mark_all_read()
        return marked, await self.repository.unread_count()

    # ------------------------------------------------------------------ writing a notice

    async def _role_id(self, code: str) -> UUID | None:
        role = await self.roles.get_by_code(code)
        return role.id if role is not None and role.is_active else None

    async def _roles(self, codes: list[str]) -> list[UUID | None]:
        return [await self._role_id(code) for code in codes]

    def _values(
        self, kind: NotificationKind, title: str, body: str, ticket: Ticket | None
    ) -> dict[str, Any]:
        operation = None
        if ticket is not None:
            operation = ticket.code if ticket.type.is_root else ticket.parent_code
        return {
            "kind": kind.value,
            "title": title[:TITLE_MAX],
            "body": body[:BODY_MAX],
            "ticket_id": ticket.id if ticket is not None else None,
            "operation_code": operation,
        }

    async def notify(
        self,
        kind: NotificationKind,
        title: str,
        body: str,
        ticket: Ticket | None = None,
        roles: list[UUID | None] | None = None,
        users: list[UUID | None] | None = None,
        independent: bool = False,
    ) -> Notification | None:
        """Record a notice. No recipients means no notice (it would be invisible), and an
        equal one about the same ticket in the last minutes is the same notice.

        `independent` writes in a transaction of its own, for facts about a refused request."""
        role_ids, user_ids = _unique(roles or []), _unique(users or [])
        if not role_ids and not user_ids:
            return None
        values = self._values(kind, title, body, ticket)
        if independent:
            await self.repository.add_independent(values, role_ids, user_ids)
            return None
        if await self.repository.is_duplicate(values["kind"], values["ticket_id"], values["body"]):
            return None
        return await self.repository.add(values, role_ids, user_ids)

    # ------------------------------------------------------------------ ticket events

    async def on_ticket_created(self, ticket: Ticket) -> None:
        """Tell the assignee, and whoever must act on this kind of ticket."""
        parent = ticket.parent_code or NO_OPERATION
        specific: list[tuple[NotificationKind, str, str, list[str]]] = []
        if ticket.type.code == TYPE_INAC:
            specific.append(
                (
                    NotificationKind.NAVAID_PENDING,
                    "Permiso INAC pendiente",
                    f"{ticket.code} ({parent}) tiene requisitos del INAC pendientes",
                    [ROLE_OPERATIONS],
                )
            )
        if ticket.type.code == TYPE_TECHNICAL_REPORT:
            specific.append(
                (
                    NotificationKind.TECHNICAL_REPORT,
                    "Reporte técnico cargado",
                    f"{ticket.code} ({parent}) · {ticket.title}",
                    [ROLE_MAINTENANCE],
                )
            )
        covered: set[UUID] = set()
        for kind, title, body, codes in specific:
            role_ids = _unique(await self._roles(codes))
            covered.update(role_ids)
            await self.notify(kind, title, body, ticket, roles=list(role_ids))

        # The assignment notice is skipped for a role the specific one already reached:
        # two equal notices for the same person are noise.
        roles = [r for r in _unique([ticket.assigned_role_id]) if r not in covered]
        users = _unique([ticket.assigned_user_id])
        if ticket.type.is_root:
            # The pilot fills the flight log of every operation. That notice is about the
            # operation, not about the empty technical report the system opens beside it.
            pilot = await self._role_id(ROLE_PILOT)
            if pilot is not None and pilot not in covered and pilot not in roles:
                roles.append(pilot)
            title = "Nueva operación asignada"
            role = ticket.assigned_role_name or NO_ROLE
            body = f"{ticket.code} · {ticket.title} · asignada a {role}"
            kind = NotificationKind.OPERATION_CREATED
        else:
            title = "Ticket asignado"
            body = f"{ticket.code} ({parent}) · {ticket.title}"
            kind = NotificationKind.TICKET_ASSIGNED
        await self.notify(kind, title, body, ticket, roles=list(roles), users=list(users))

    async def on_status_changed(self, ticket: Ticket) -> None:
        if ticket.type.code == TYPE_INAC and not ticket.status.is_final:
            await self.notify(
                NotificationKind.NAVAID_PENDING,
                "Permiso INAC pendiente",
                f'{ticket.code} ({ticket.parent_code or NO_OPERATION}) pasó a "{ticket.status.name}"',
                ticket,
                roles=await self._roles([ROLE_OPERATIONS]),
            )
        if ticket.type.is_root and ticket.status.is_final and ticket.status.counts_as_committed:
            # Cancelling is final too, but it is not a signed close.
            await self.notify(
                NotificationKind.OPERATION_CLOSED,
                "Operación cerrada y firmada",
                f"{ticket.code} · {ticket.title}",
                ticket,
                roles=await self._roles([ROLE_OPERATIONS, ROLE_ADMIN, *CREW_ROLES]),
                users=[ticket.assigned_user_id],
            )

    async def on_close_blocked(self, operation: Ticket, blockers: list[Ticket]) -> None:
        inac = [b for b in blockers if b.type.code == TYPE_INAC]
        if not inac:
            return
        codes = ", ".join(b.code for b in inac)
        await self.notify(
            NotificationKind.CLOSE_BLOCKED,
            "Cierre bloqueado por permiso INAC",
            f"{operation.code} no puede cerrarse: {codes} sin resolver",
            operation,
            roles=await self._roles([ROLE_OPERATIONS]),
            independent=True,
        )

    async def on_receipt_to_validate(self, ticket: Ticket, file_name: str) -> None:
        await self.notify(
            NotificationKind.RECEIPT_VALIDATION,
            "Comprobante por validar",
            f"{ticket.code} ({ticket.parent_code or NO_OPERATION}) · {file_name}",
            ticket,
            roles=await self._roles([ROLE_ADMIN]),
        )
