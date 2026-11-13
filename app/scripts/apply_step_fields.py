"""Fill step_code on existing ticket-type fields, only where it is still empty.

`python -m app.scripts.apply_step_fields`. The seed never overwrites a type that
already exists, so a database created before a field named its step keeps
step_code null and that field stays on the shared form. This copies the step
from seeds.json when the type still has that step and the field's step_code is
null. A step an administrator already chose is left as is.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.scripts.apply_aircraft_catalogs import _as_dict
from app.scripts.init import SeedReport
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.seed_loader import load_seeds
from app.utils.type_schema import normalize_fields

logger = logging.getLogger(__name__)


async def _upgrade(session: AsyncSession, report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    for definition in load_seeds().ticket_types:
        wanted = {
            field["key"]: str(field["step_code"]).strip()
            for field in definition.get("fields", [])
            if field.get("step_code")
        }
        if not wanted:
            continue
        record = await types.get_by_code(definition["code"])
        if record is None:
            continue
        step_codes = {step.code for step in record.steps}
        current = sorted(record.fields, key=lambda field: field.sort_order)
        rows = {field.key: _as_dict(field) for field in current}
        changed = False
        for key, code in wanted.items():
            if key not in rows or code not in step_codes or rows[key]["step_code"]:
                continue
            rows[key]["step_code"] = code
            changed = True
            report.add(f"{definition['code']}_{key}_step")
        if changed:
            await types.replace_collections(
                record,
                normalize_fields([rows[field.key] for field in current]),
                None,
                None,
            )


async def run_upgrade(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    with without_audit():
        await _upgrade(session, report)
    if report.total:
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.TICKET_TYPE,
                entity_id="field-step-code",
                entity_label="Paso de cada campo",
                summary="Asignó el paso de los campos que aún no tenían uno",
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
    print(f"field steps: {result.created or 'already applied'}")
