from __future__ import annotations

from typing import Any

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.exceptions.admin import AdminError
from app.models.access import Permission, Role
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.services.base import ProjectService
from app.utils.slug import slugify


class RoleService(ProjectService[Role]):
    repository: RoleRepository
    permissions: PermissionRepository

    filter_whitelist = frozenset({"is_active", "is_system"})
    search_fields = ["name"]

    async def _resolve_permissions(self, codes: list[str]) -> list[Permission]:
        """Catalog entries for `codes`, enforcing that the caller holds each of them."""
        wanted = list(dict.fromkeys(codes))
        found = await self.permissions.by_codes(wanted)
        unknown = [c for c in wanted if c not in found]
        if unknown:
            raise AdminError(
                "validation", f"Permisos que no existen: {', '.join(unknown)}.", field="permissions"
            )
        return [found[c] for c in wanted]

    def _require_held(self, permissions: list[Permission]) -> None:
        """A role manager cannot hand out permissions they do not hold themselves."""
        missing = [p.code for p in permissions if p.code not in self.principal.permissions]
        if missing:
            raise AdminError(
                "privilege",
                f"No puedes conceder permisos que tú no tienes: {', '.join(missing)}.",
                field="permissions",
            )

    async def _unique_code(self, name: str) -> str:
        base = slugify(name) or "rol"
        code, suffix = base, 2
        while await self.repository.code_taken(code):
            code, suffix = f"{base}_{suffix}", suffix + 1
        return code

    async def create(self, payload, check_fields=None) -> Role:
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        name = data["name"].strip()
        if await self.repository.name_taken(name):
            raise AdminError("duplicate", "Ya existe un rol con ese nombre.", field="name")
        permissions = await self._resolve_permissions(data.get("permissions") or [])
        self._require_held(permissions)
        values = {
            "name": name,
            "description": data.get("description"),
            "code": await self._unique_code(name),
            "is_system": False,
            "is_active": True,
            "has_all_permissions": False,
        }
        return await self.repository.create_with_permissions(values, permissions)

    async def update(self, id, data) -> Role:  # noqa: A002
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else dict(data)
        role = await self.repository.get_scoped(id)
        if role is None:
            raise NotFoundException(f"id={id} no encontrado")

        if "name" in payload:
            name = (payload["name"] or "").strip()
            if await self.repository.name_taken(name, exclude_id=role.id):
                raise AdminError("duplicate", "Ya existe un rol con ese nombre.", field="name")
            payload["name"] = name
        if payload.get("is_active") is False:
            if role.has_all_permissions:
                raise AdminError("protected", "El rol de superusuario no puede desactivarse.")
            if role.id == self.principal.role_id:
                raise AdminError("protected", "No puedes desactivar tu propio rol.")

        new_permissions: list[Permission] | None = None
        requested = payload.pop("permissions", None)
        if requested is not None:
            if role.has_all_permissions:
                raise AdminError(
                    "protected",
                    "Los permisos del rol de superusuario no se pueden modificar.",
                    field="permissions",
                )
            new_permissions = await self._resolve_permissions(requested)
            current = {p.code for p in role.permissions}
            self._require_held([p for p in new_permissions if p.code not in current])

        scalar = {k: v for k, v in payload.items() if k in ("name", "description", "is_active")}
        if "is_active" in scalar and scalar["is_active"] is None:
            raise AdminError(
                "validation", "Este campo no admite valores vacíos.", field="is_active"
            )
        updated = await super().update(id, scalar) if scalar else role
        if new_permissions is not None:
            updated = await self.repository.replace_permissions(updated, new_permissions)
        return updated

    async def delete(self, id) -> bool:  # noqa: A002
        role = await self.repository.get_scoped(id)
        if role is None:
            raise NotFoundException(f"id={id} no encontrado")
        if role.is_system or role.has_all_permissions:
            raise AdminError("protected", "Ese rol es del sistema y no puede eliminarse.")
        if await self.repository.has_users(role.id):
            raise AdminError(
                "in_use", "El rol tiene usuarios asignados. Reasígnalos antes de eliminarlo."
            )
        return await super().delete(id)
