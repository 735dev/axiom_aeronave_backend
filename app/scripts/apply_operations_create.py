"""Gerencia de Operaciones creates operation tickets and does not edit the catalog.

`python -m app.scripts.apply_operations_create`. Idempotent.

The seed never rewrites a role that already exists. This script:

1. grants `tickets.create.operation` when that permission exists;
2. removes the global `tickets.create`, `ticket_types.manage` and the unused
   `operations.clearance` mark from the `operations` role.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.scripts.init import SeedReport
from app.utils.audit_context import SYSTEM_ACTOR, without_audit

logger = logging.getLogger(__name__)

ROLE = "operations"
GRANT = "tickets.create.operation"
REVOKE = ("tickets.create", "ticket_types.manage", "operations.clearance")


async def run_upgrade(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    with without_audit():
        roles = RoleRepository(session)
        permissions = PermissionRepository(session)
        role = await roles.get_by_code(ROLE)
        grant = await permissions.get_by_code(GRANT)
        if role is None or grant is None:
            raise RuntimeError(f"Role {ROLE} or permission {GRANT} is missing")
        held = list(role.permissions)
        codes = {item.code for item in held}
        changed = False
        if GRANT not in codes:
            held.append(grant)
            changed = True
            report.add("role_grants")
        kept = [item for item in held if item.code not in REVOKE]
        if len(kept) != len(held):
            changed = True
            report.add("role_revokes")
        if changed:
            await roles.replace_permissions(role, kept)
    if report.total:
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(report.created.items()))
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.ROLE,
                entity_id="operations-create",
                entity_label="Gerencia de Operaciones",
                summary=f"Crear solo operaciones: {detail}",
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
    print(f"operations create: {result.created or 'already applied'}")
