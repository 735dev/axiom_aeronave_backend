from __future__ import annotations

from app.config.audit import AuditEntity
from app.models.access import Module
from app.repositories.base import AuditedRepository


class ModuleRepository(AuditedRepository[Module]):
    model = Module
    default_ordering = ("sort_order", "code")
    audit_entity = AuditEntity.MODULE
    audit_labels = {
        "name": "Nombre",
        "description": "Descripción",
        "route": "Ruta",
        "icon": "Icono",
        "menu_permission": "Permiso del menú",
        "show_in_menu": "Visible en el menú",
        "sort_order": "Orden",
        "is_active": "Activo",
    }

    async def get_by_code(self, code: str) -> Module | None:
        return await self.get_by_field("code", code)
