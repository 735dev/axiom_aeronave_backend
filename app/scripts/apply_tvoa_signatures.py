"""Idempotent upgrade of an EXISTING database to the TVOA multi-signature closing:
`python -m app.scripts.apply_tvoa_signatures`.

The seed never overwrites what already exists, so a database created before this change
keeps the old closing (one signature of Operaciones) and the ASEC steps assigned to
Operaciones. This script, run once per database (and harmless to repeat):

1. creates the permission `tickets.sign` and the role `avsec` (with the permissions of the
   seed) when missing, and grants `tickets.sign` to the four roles that sign;
2. on the operation type, replaces the single `signature` step with the four signing steps
   of the seed (captain, maintenance, AVSEC, general manager), keeping every other step;
3. on the technical report type, hands the steps `movement` and `return` from Operaciones
   to `avsec` (only while they still belong to Operaciones: a later edit is respected).

Operations already closed keep their seal untouched. Decisions recorded on the old
`signature` step stay in place but no longer count: an open operation needs the four new
signatures. Demo users are NOT created here (use `make seed` locally).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.role import RoleRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.repositories.permission import PermissionRepository
from app.scripts.init import SeedReport, _seed_permissions, _seed_roles
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.seed_loader import load_seeds
from app.utils.type_schema import normalize_steps

logger = logging.getLogger(__name__)

OPERATION_TYPE = "operation"
REPORT_TYPE = "technical_report"
OLD_SIGNATURE_STEP = "signature"
OLD_OWNER_ROLE = "operations"
NEW_OWNER_ROLE = "avsec"
HANDED_OVER_STEPS = ("movement", "return")
SIGN_PERMISSION = "tickets.sign"
SIGNING_ROLES = ("pilot", "maintenance", "avsec", "executive")


def _as_dict(step: Any) -> dict[str, Any]:
    return {
        "code": step.code,
        "name": step.name,
        "role_id": step.role_id,
        "responsible_area": step.responsible_area,
        "required": step.required,
        "requires_signature": step.requires_signature,
    }


async def _grant_sign_permission(session: AsyncSession, report: SeedReport) -> None:
    roles, permissions = RoleRepository(session), PermissionRepository(session)
    permission = await permissions.get_by_code(SIGN_PERMISSION)
    if permission is None:
        raise RuntimeError(f"Permission {SIGN_PERMISSION} was not created")
    for code in SIGNING_ROLES:
        role = await roles.get_by_code(code)
        if role is None or any(p.code == SIGN_PERMISSION for p in role.permissions):
            continue
        await roles.replace_permissions(role, [*role.permissions, permission])
        report.add("sign_permission_grants")


async def _upgrade_operation(session: AsyncSession, seed: dict, report: SeedReport) -> None:
    types, roles = TicketTypeRepository(session), RoleRepository(session)
    record = await types.get_by_code(OPERATION_TYPE)
    if record is None:
        return
    wanted = [s for s in seed["steps"] if s.get("requires_signature")]
    have = {s.code for s in record.steps}
    if not (OLD_SIGNATURE_STEP in have or any(s["code"] not in have for s in wanted)):
        return
    steps = [_as_dict(s) for s in record.steps if s.code != OLD_SIGNATURE_STEP]
    for item in wanted:
        if item["code"] in have:
            continue
        role = await roles.get_by_code(item["role_code"])
        if role is None:
            raise RuntimeError(f"Signing step {item['code']} points to unknown role")
        steps.append(
            {
                "code": item["code"],
                "name": item["name"],
                "role_id": role.id,
                "responsible_area": item.get("responsible_area"),
                "required": item.get("required", True),
                "requires_signature": True,
            }
        )
    await types.replace_collections(record, None, None, normalize_steps(steps))
    report.add("operation_signing_steps")


async def _upgrade_report(session: AsyncSession, report: SeedReport) -> None:
    types, roles = TicketTypeRepository(session), RoleRepository(session)
    record = await types.get_by_code(REPORT_TYPE)
    old, new = await roles.get_by_code(OLD_OWNER_ROLE), await roles.get_by_code(NEW_OWNER_ROLE)
    if record is None or old is None or new is None:
        return
    area = next(
        (
            s.get("responsible_area")
            for t in load_seeds().ticket_types
            if t["code"] == REPORT_TYPE
            for s in t["steps"]
            if s["code"] in HANDED_OVER_STEPS and s.get("responsible_area")
        ),
        None,
    )
    steps, moved = [], 0
    for step in record.steps:
        item = _as_dict(step)
        if step.code in HANDED_OVER_STEPS and step.role_id == old.id:
            item["role_id"] = new.id
            item["responsible_area"] = area or item["responsible_area"]
            moved += 1
        steps.append(item)
    if moved:
        await types.replace_collections(record, None, None, normalize_steps(steps))
        report.add("report_steps_to_avsec", moved)


async def run_upgrade(session: AsyncSession) -> SeedReport:
    data = load_seeds()
    report = SeedReport()
    operation = next(t for t in data.ticket_types if t["code"] == OPERATION_TYPE)
    with without_audit():
        # Both only create what is missing (here: tickets.sign and the role avsec).
        await _seed_permissions(session, data, report)
        await _seed_roles(session, data, report, set())
        await _grant_sign_permission(session, report)
        await _upgrade_operation(session, operation, report)
        await _upgrade_report(session, report)
    if report.total:
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(report.created.items()))
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.TICKET_TYPE,
                entity_id="tvoa-signatures",
                entity_label="Firmas TVOA",
                summary=f"Cierre con cuatro firmas y rol AVSEC: {detail}",
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
    print(f"tvoa signatures: {result.created or 'already applied'}")
