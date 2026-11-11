"""Idempotent upgrade of an EXISTING database to the per-role passengers lookup:
`python -m app.scripts.apply_passengers_permission`.

The seed never overwrites what already exists. Run once per database (harmless to repeat):

1. creates the permission `passengers.view` when missing;
2. points the menu entry `passengers` at it (only while it still uses `tickets.view`:
   a later edit made in the roles screen is respected);
3. grants it to `operations` and `avsec` (the seed roles that consult people). Any other
   role gets it from the roles screen, which is the point: the system does not decide.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.module import ModuleRepository
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.scripts.init import SeedReport, _seed_permissions
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.seed_loader import load_seeds

logger = logging.getLogger(__name__)

PERMISSION = "passengers.view"
MODULE = "passengers"
OLD_MENU_PERMISSION = "tickets.view"
GRANTED_ROLES = ("operations", "avsec")


async def run_upgrade(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    with without_audit():
        await _seed_permissions(session, load_seeds(), report)
        permission = await PermissionRepository(session).get_by_code(PERMISSION)
        if permission is None:
            raise RuntimeError(f"Permission {PERMISSION} was not created")
        module = await ModuleRepository(session).get_by_code(MODULE)
        if module is not None and module.menu_permission == OLD_MENU_PERMISSION:
            module.menu_permission = PERMISSION
            await session.flush()
            report.add("menu_permission")
        roles = RoleRepository(session)
        for code in GRANTED_ROLES:
            role = await roles.get_by_code(code)
            if role is None or any(p.code == PERMISSION for p in role.permissions):
                continue
            await roles.replace_permissions(role, [*role.permissions, permission])
            report.add("role_grants")
    if report.total:
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(report.created.items()))
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.ROLE,
                entity_id="passengers-permission",
                entity_label="Permiso de pasajeros",
                summary=f"Consulta de pasajeros por permiso: {detail}",
                actor=SYSTEM_ACTOR,
            )
        )
    return report


async def main() -> SeedReport:
    async with database.AsyncSessionFactory() as session:
        report = await run_upgrade(session)
        await session.commit()
    await database.close_db()
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(main())
    print(f"passengers permission: {result.created or 'already applied'}")
