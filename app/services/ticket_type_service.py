"""Ticket types: the dynamic definition (fields, statuses, steps) and its permissions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.exceptions.tickets import TicketError
from app.models.ticket import TicketType
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.services.base import ProjectService
from app.services.permission_service import PermissionService
from app.utils.type_schema import (
    normalize_code,
    normalize_fields,
    normalize_statuses,
    normalize_steps,
)


class TicketTypeService(ProjectService[TicketType]):
    repository: TicketTypeRepository
    roles: RoleRepository
    permissions: PermissionRepository

    filter_whitelist = frozenset({"is_active", "is_root"})
    search_fields = ["name", "code"]

    async def _check_role(self, role_id: UUID | None, field: str) -> None:
        if role_id is None:
            return
        role = await self.roles.get(role_id)
        if role is None or not role.is_active:
            raise TicketError("validation", "El rol indicado no es válido.", field)

    async def _check_children(
        self, codes: list[str], is_root: bool, own_code: str | None
    ) -> list[str]:
        unique = list(dict.fromkeys(codes))
        if unique and not is_root:
            raise TicketError(
                "validation", "Solo un tipo raíz puede tener tipos hijos.", "child_type_codes"
            )
        if own_code in unique:
            raise TicketError(
                "validation", "Un tipo no puede ser hijo de sí mismo.", "child_type_codes"
            )
        found = await self.repository.by_codes(unique)
        missing = [c for c in unique if c not in found]
        if missing:
            raise TicketError(
                "validation",
                f"Tipos hijos que no existen: {', '.join(missing)}.",
                "child_type_codes",
            )
        roots = [c for c in unique if found[c].is_root]
        if roots:
            raise TicketError(
                "validation",
                f"Un tipo raíz no puede ser hijo: {', '.join(roots)}.",
                "child_type_codes",
            )
        return unique

    async def _definition(self, body: dict[str, Any]) -> dict[str, Any]:
        """Normalise the collections present in `body` and check the roles they point at."""
        result: dict[str, Any] = {}
        if body.get("fields") is not None:
            result["fields"] = normalize_fields(body["fields"])
        if body.get("statuses") is not None:
            result["statuses"] = normalize_statuses(body["statuses"])
        if body.get("steps") is not None:
            result["steps"] = normalize_steps(body["steps"])
            for step in result["steps"]:
                await self._check_role(step["role_id"], "steps")
        return result

    async def create(self, payload, check_fields=None) -> TicketType:
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else dict(payload)
        name = body["name"].strip()
        code = normalize_code(name, body.get("code"))
        if await self.repository.code_taken(code):
            raise TicketError("validation", "Ya existe un tipo con ese código.", "code")
        await self._check_role(body.get("default_role_id"), "default_role_id")
        definition = await self._definition(body)
        values = {
            "code": code,
            "name": name,
            "description": body.get("description"),
            "is_root": body["is_root"],
            "child_type_codes": await self._check_children(
                body.get("child_type_codes") or [], body["is_root"], code
            ),
            "default_role_id": body.get("default_role_id"),
            "blocks_parent_close": body["blocks_parent_close"],
            "relation_kinds": list(dict.fromkeys(body.get("relation_kinds") or [])),
            "is_system": False,
            "is_active": True,
        }
        created = await self.repository.create_type(
            values,
            definition["fields"],
            definition["statuses"],
            definition.get("steps", []),
        )
        await PermissionService(self.permissions, request=self.request).register_type_permissions(
            created.code, created.name
        )
        return created

    async def update(self, id, data) -> TicketType:  # noqa: A002
        body = (
            data.model_dump(mode="json", exclude_unset=True)
            if hasattr(data, "model_dump")
            else dict(data)
        )
        record = await self.repository.get(id)
        if record is None:
            raise NotFoundException(f"id={id} no encontrado")

        previous_name = record.name
        scalar: dict[str, Any] = {}
        for key in ("description", "default_role_id"):
            if key in body:
                scalar[key] = body[key]
        for key in ("name", "is_root", "blocks_parent_close", "is_active", "relation_kinds"):
            if body.get(key) is not None:
                scalar[key] = body[key]
        if "name" in scalar:
            scalar["name"] = scalar["name"].strip()
        if "default_role_id" in scalar:
            await self._check_role(scalar["default_role_id"], "default_role_id")
        if "relation_kinds" in scalar:
            scalar["relation_kinds"] = list(dict.fromkeys(scalar["relation_kinds"]))

        is_root = scalar.get("is_root", record.is_root)
        if "is_root" in scalar and scalar["is_root"] != record.is_root:
            if await self.repository.has_tickets(record.id):
                raise TicketError(
                    "in_use",
                    "No se puede cambiar si el tipo es raíz: ya tiene tickets.",
                    "is_root",
                )
        if "child_type_codes" in body and body["child_type_codes"] is not None:
            scalar["child_type_codes"] = await self._check_children(
                body["child_type_codes"], is_root, record.code
            )
        elif not is_root and record.child_type_codes:
            scalar["child_type_codes"] = []
        if scalar.get("is_active") is False and record.is_system:
            raise TicketError("protected", "Un tipo del sistema no se puede desactivar.")

        definition = await self._definition(body)
        if "statuses" in definition:
            await self._check_statuses(record, definition["statuses"])
        updated = await super().update(id, scalar) if scalar else record
        if definition:
            updated = await self.repository.replace_collections(
                updated,
                definition.get("fields"),
                definition.get("statuses"),
                definition.get("steps"),
            )
        if "name" in scalar and scalar["name"] != previous_name:
            await PermissionService(self.permissions, request=self.request).refresh_names(
                updated.code, updated.name
            )
        return updated

    async def _check_statuses(self, record: TicketType, statuses: list[dict[str, Any]]) -> None:
        """A status that tickets sit in cannot disappear from the definition."""
        wanted = {s["code"] for s in statuses}
        in_use = await self.repository.status_codes_in_use(record.id)
        lost = sorted(in_use - wanted)
        if lost:
            raise TicketError(
                "in_use",
                f"Hay tickets en estos estados y no se pueden quitar: {', '.join(lost)}.",
                "statuses",
            )

    async def delete(self, id) -> bool:  # noqa: A002
        record = await self.repository.get(id)
        if record is None:
            raise NotFoundException(f"id={id} no encontrado")
        if record.is_system:
            raise TicketError("protected", "Un tipo del sistema no se puede eliminar.")
        if await self.repository.has_tickets(record.id):
            raise TicketError(
                "in_use", "El tipo ya tiene tickets: desactívalo en lugar de eliminarlo."
            )
        parents = await self.repository.types_listing_child(record.code, exclude_id=record.id)
        await self.repository.remove_from_children(record.code, parents)
        code = record.code
        deleted = await super().delete(id)
        await PermissionService(self.permissions, request=self.request).unregister_type_permissions(
            code
        )
        return deleted
