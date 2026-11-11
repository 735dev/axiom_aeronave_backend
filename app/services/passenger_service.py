"""Passengers of an operation's manifest and the search across operations (§6.17)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.closing import MAX_PASSENGER_QUERY
from app.exceptions.closing import PassengerError
from app.exceptions.tickets import TicketError
from app.models.passenger import TicketPassenger
from app.models.ticket import Ticket
from app.repositories.passenger import PassengerMatch, TicketPassengerRepository
from app.repositories.ticket_support import TicketSupportRepository
from app.services.base import ProjectService
from app.utils.passengers import clean_query, document_query, fold_text, normalize_document
from app.utils.ticket_access import can_edit, viewable_type_codes

NO_EDIT_MESSAGE = "No tienes permiso para editar este tipo de registro."


class PassengerService(ProjectService[Ticket]):
    repository: TicketPassengerRepository
    supports: TicketSupportRepository

    async def _operation(self, id: UUID, lock: bool = False) -> Ticket:  # noqa: A002
        ticket = await (self.repository.get_locked(id) if lock else self.repository.get(id))
        if ticket is None or not ticket.type.is_root:
            raise NotFoundException("no encontrado")
        return ticket

    async def manifest(self, id: UUID) -> list[TicketPassenger]:  # noqa: A002
        ticket = await self._operation(id)
        return await self.repository.manifest(ticket.id)

    async def replace(self, id: UUID, data: Any) -> list[TicketPassenger]:  # noqa: A002
        body = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        ticket = await self._operation(id, lock=True)
        if not can_edit(self.principal.permissions, ticket.type.code):
            raise TicketError("forbidden", NO_EDIT_MESSAGE)
        if ticket.status.is_final:
            await self.repository.record_denied(ticket, "actualizar el manifiesto de")
            raise TicketError("locked", f"{ticket.code} está cerrado y no admite cambios.")

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(body["passengers"]):
            name = " ".join(item["full_name"].split())
            document = item["document"].strip()
            key = normalize_document(document)
            if not name or not key:
                raise PassengerError(
                    "validation", "Cada pasajero necesita nombre y documento.", "passengers"
                )
            if key in seen:
                raise PassengerError(
                    "validation",
                    f"El documento {document} está repetido en el manifiesto.",
                    "passengers",
                )
            seen.add(key)
            reference = item.get("passport_file_id")
            if reference is not None and not await self.supports.belongs_to(ticket.id, reference):
                # Never trust a reference from the client: it must be a support of THIS
                # operation, or a manifest could point at another expedient's file.
                raise PassengerError(
                    "validation",
                    "El pasaporte debe ser un soporte de esta operación.",
                    "passengers",
                )
            rows.append(
                {
                    "full_name": name,
                    "document": document,
                    "nationality": item.get("nationality"),
                    "passport_ref": reference,
                    "name_key": fold_text(name),
                    "document_key": key,
                }
            )
        return await self.repository.replace_manifest(ticket, rows)

    async def search(self, query: str) -> list[PassengerMatch]:
        """Scope first: with no readable operation type the answer is 404, the same one a
        missing resource gives (a 403 would confirm there is data to protect)."""
        await self.throttle("search")
        held = self.principal.permissions
        viewable = viewable_type_codes(held)
        if viewable is not None and not viewable & await self.repository.root_type_codes():
            raise NotFoundException("no encontrado")
        text = clean_query(query[:MAX_PASSENGER_QUERY])
        name_key = fold_text(text)
        if not name_key:
            # No text: the most recent people. Who may see this is the permission's job.
            return await self.repository.search(None, None)
        return await self.repository.search(name_key, document_query(text))
