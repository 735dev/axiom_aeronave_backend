"""Permission catalog access.

The catalog is closed: there is no create/update/delete exposed to the API. The only
runtime writes are the permissions derived from a ticket type, through the explicit
`add_derived` / `remove_derived` methods that the ticket-type module calls.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, insert, select

from app.config.access import TICKETS_MODULE_CODE
from app.config.audit import AuditAction, AuditEntity
from app.models.access import Module, Permission, Role, role_permission
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.base import ProjectRepository
from app.utils.audit_context import audit_is_suppressed
from app.utils.permission_labels import permission_label


class PermissionRepository(ProjectRepository[Permission]):
    model = Permission
    default_ordering = ("code",)

    async def get_by_code(self, code: str) -> Permission | None:
        return await self.get_by_field("code", code)

    async def by_codes(self, codes: Iterable[str]) -> dict[str, Permission]:
        wanted = set(codes)
        if not wanted:
            return {}
        result = await self.session.execute(select(Permission).where(Permission.code.in_(wanted)))
        return {p.code: p for p in result.scalars().all()}

    async def codes_exist(self, codes: Iterable[str]) -> bool:
        wanted = set(codes)
        return set(await self.by_codes(wanted)) == wanted

    async def all_permissions(self) -> list[Permission]:
        result = await self.session.execute(select(Permission).order_by(Permission.code))
        return list(result.scalars().all())

    async def create_seeded(
        self, code: str, name: str, module: Module, description: str | None, is_system: bool
    ) -> Permission:
        permission = Permission(
            code=code,
            name=name,
            description=description,
            module_id=module.id,
            is_system=is_system,
            is_active=True,
        )
        self.session.add(permission)
        await self.session.flush()
        await self.session.refresh(permission)
        return permission

    async def add_derived(self, code: str, name: str, description: str) -> Permission | None:
        """Create a ticket-type permission (idempotent). The superuser role gets it too."""
        existing = await self.get_by_code(code)
        if existing is not None:
            return existing
        module = (
            await self.session.execute(select(Module).where(Module.code == TICKETS_MODULE_CODE))
        ).scalar_one()
        permission = await self.create_seeded(code, name, module, description, is_system=False)
        await self.link_to_all_permission_roles([permission])
        return permission

    async def link_to_all_permission_roles(self, permissions: Iterable[Permission]) -> None:
        roles = (
            (await self.session.execute(select(Role).where(Role.has_all_permissions.is_(True))))
            .scalars()
            .all()
        )
        for role in roles:
            have = {p.id for p in role.permissions}
            rows: list[dict[str, Any]] = [
                {"role_id": role.id, "permission_id": p.id} for p in permissions if p.id not in have
            ]
            if rows:
                await self.session.execute(insert(role_permission), rows)
        await self.session.flush()

    async def sync_superuser_roles(self) -> int:
        """Make every has_all_permissions role hold the whole catalog. Returns links added."""
        everything = await self.all_permissions()
        roles = (
            (await self.session.execute(select(Role).where(Role.has_all_permissions.is_(True))))
            .scalars()
            .all()
        )
        added = 0
        for role in roles:
            have = {p.id for p in role.permissions}
            rows = [
                {"role_id": role.id, "permission_id": p.id} for p in everything if p.id not in have
            ]
            if rows:
                await self.session.execute(insert(role_permission), rows)
                added += len(rows)
        await self.session.flush()
        return added

    async def rename_derived(self, code: str, name: str, description: str) -> None:
        permission = await self.get_by_code(code)
        if permission is not None and (permission.name, permission.description) != (
            name,
            description,
        ):
            permission.name = name
            permission.description = description
            await self.session.flush()

    async def remove_derived(self, codes: Iterable[str]) -> None:
        """Delete ticket-type permissions; the DB cascades the role and user links.

        Each role that held one gets a permission_change event, so the trail explains
        why its permission list shrank.
        """
        wanted = list(codes)
        if not wanted:
            return
        permissions = await self.by_codes(wanted)
        if not permissions:
            return
        ids = [p.id for p in permissions.values()]
        affected = (
            (
                await self.session.execute(
                    select(Role)
                    .join(role_permission, role_permission.c.role_id == Role.id)
                    .where(role_permission.c.permission_id.in_(ids))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if not audit_is_suppressed():
            events = AuditEventRepository(self.session)
            for role in affected:
                lost = [p for p in role.permissions if p.id in ids]
                await events.append(
                    AuditEntry(
                        action=AuditAction.PERMISSION_CHANGE,
                        entity=AuditEntity.ROLE,
                        entity_id=str(role.id),
                        entity_label=role.name,
                        summary=f"Se retiraron {len(lost)} permiso(s) derivados del rol {role.name}",
                        changes=[
                            {
                                "field": "permission",
                                "label": permission_label(p),
                                "from": "concedido",
                                "to": None,
                            }
                            for p in lost
                        ],
                    )
                )
        await self.session.execute(delete(Permission).where(Permission.id.in_(ids)))
        await self.session.flush()
