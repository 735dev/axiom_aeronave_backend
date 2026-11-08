from __future__ import annotations

from typing import Any

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.access import MODULE_EDITABLE_FIELDS
from app.exceptions.admin import AdminError
from app.models.access import Module
from app.repositories.module import ModuleRepository
from app.repositories.permission import PermissionRepository
from app.services.base import ProjectService


class ModuleService(ProjectService[Module]):
    repository: ModuleRepository
    permissions: PermissionRepository

    async def update(self, id, data) -> Module:  # noqa: A002 - basekit signature
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else dict(data)
        changes: dict[str, Any] = {k: v for k, v in payload.items() if k in MODULE_EDITABLE_FIELDS}
        if await self.repository.get_scoped(id) is None:
            raise NotFoundException(f"id={id} no encontrado")
        for field in ("name", "sort_order", "show_in_menu", "is_active"):
            if field in changes and changes[field] is None:
                raise AdminError("validation", "Este campo no admite valores vacíos.", field=field)
        code = changes.get("menu_permission")
        if code and await self.permissions.get_by_code(code) is None:
            raise AdminError(
                "validation",
                "El permiso del menú no existe en el catálogo.",
                field="menu_permission",
            )
        return await super().update(id, changes)
