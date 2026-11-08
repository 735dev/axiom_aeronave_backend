from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select

from app.config.audit import AuditAction, AuditEntity
from app.models.access import AppUser, Permission, Role
from app.repositories.audit_trail import AuditEntry
from app.repositories.base import AuditedRepository
from app.utils.permission_labels import GRANTED_LABEL, permission_label


class RoleRepository(AuditedRepository[Role]):
    model = Role
    default_ordering = ("name",)
    audit_entity = AuditEntity.ROLE
    audit_labels = {
        "name": "Nombre",
        "description": "Descripción",
        "is_active": "Activo",
    }

    async def get_by_code(self, code: str) -> Role | None:
        return await self.get_by_field("code", code)

    async def name_taken(self, name: str, exclude_id: Any = None) -> bool:
        query = select(Role.id).where(func.lower(Role.name) == name.strip().lower())
        if exclude_id is not None:
            query = query.where(Role.id != exclude_id)
        return (await self.session.execute(query.limit(1))).first() is not None

    async def code_taken(self, code: str) -> bool:
        query = select(Role.id).where(Role.code == code).limit(1)
        return (await self.session.execute(query)).first() is not None

    async def has_users(self, role_id: Any) -> bool:
        query = select(AppUser.id).where(AppUser.role_id == role_id).limit(1)
        return (await self.session.execute(query)).first() is not None

    async def create_with_permissions(
        self, data: dict[str, Any], permissions: Sequence[Permission]
    ) -> Role:
        role = Role(**data)
        role.permissions = list(permissions)
        created = await super().create(role)
        if permissions:
            await self._emit_permission_change(
                created,
                list(permissions),
                [],
                f"Asignó {len(permissions)} permiso(s) al rol {created.name}",
            )
        return created

    async def replace_permissions(self, role: Role, permissions: Sequence[Permission]) -> Role:
        before = {p.code: p for p in role.permissions}
        after = {p.code: p for p in permissions}
        granted = [after[c] for c in after if c not in before]
        removed = [before[c] for c in before if c not in after]
        if not granted and not removed:
            return role
        role.permissions = list(permissions)
        await self.session.flush()
        await self.session.refresh(role)
        await self._emit_permission_change(
            role,
            granted,
            removed,
            f"Cambió los permisos del rol {role.name}: +{len(granted)} / -{len(removed)}",
        )
        return role

    async def _emit_permission_change(
        self, role: Role, granted: list[Permission], removed: list[Permission], summary: str
    ) -> None:
        changes = [
            {"field": "permission", "label": permission_label(p), "from": None, "to": GRANTED_LABEL}
            for p in granted
        ] + [
            {"field": "permission", "label": permission_label(p), "from": GRANTED_LABEL, "to": None}
            for p in removed
        ]
        await self.emit(
            AuditEntry(
                action=AuditAction.PERMISSION_CHANGE,
                entity=AuditEntity.ROLE,
                entity_id=str(role.id),
                entity_label=role.name,
                summary=summary,
                changes=changes,
            )
        )
