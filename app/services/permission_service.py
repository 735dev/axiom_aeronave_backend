from __future__ import annotations

from typing import Any

from app.config.access import TYPE_PERMISSION_ACTIONS
from app.models.access import Permission
from app.repositories.permission import PermissionRepository
from app.services.base import ProjectService


class PermissionService(ProjectService[Permission]):
    """Read-only catalog, plus the derived permissions of ticket types.

    `register_type_permissions` / `unregister_type_permissions` are the API the ticket-type
    module calls; they are not reachable over HTTP.
    """

    repository: PermissionRepository
    filter_whitelist = frozenset({"module_code", "is_active"})

    def get_filters(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        applied = super().get_filters(filters)
        if "module_code" in applied:
            applied["module__code"] = applied.pop("module_code")
        return applied

    @staticmethod
    def derived_codes(type_code: str) -> list[str]:
        return [f"{base}.{type_code}" for base, _ in TYPE_PERMISSION_ACTIONS]

    async def register_type_permissions(self, type_code: str, type_name: str) -> None:
        for base, verb in TYPE_PERMISSION_ACTIONS:
            await self.repository.add_derived(
                f"{base}.{type_code}", f"{verb} · {type_name}", f"Solo para el tipo {type_name}"
            )

    async def unregister_type_permissions(self, type_code: str) -> None:
        await self.repository.remove_derived(self.derived_codes(type_code))

    async def refresh_names(self, type_code: str, type_name: str) -> None:
        """Keep the labels of a type's derived permissions in step with its name."""
        for base, verb in TYPE_PERMISSION_ACTIONS:
            await self.repository.rename_derived(
                f"{base}.{type_code}", f"{verb} · {type_name}", f"Solo para el tipo {type_name}"
            )
