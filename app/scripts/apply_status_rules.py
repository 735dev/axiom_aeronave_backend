"""Fill advance_rules on existing statuses, only where the list is still empty.

`python -m app.scripts.apply_status_rules`. The seed never overwrites a type that
already exists. This copies each status rule from seeds.json when that status
still has no rules and the type still has the step and field the rule names.
A rule an administrator already saved is left as is.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.scripts.init import SeedReport
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.seed_loader import load_seeds
from app.utils.type_schema import normalize_statuses

logger = logging.getLogger(__name__)

STATUS_COLUMNS = (
    "code",
    "name",
    "color",
    "sort_order",
    "is_initial",
    "is_final",
    "counts_as_committed",
    "next_codes",
    "advance_rules",
)


def _as_dict(status: Any) -> dict[str, Any]:
    return {column: getattr(status, column) for column in STATUS_COLUMNS}


async def _upgrade(session: AsyncSession, report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    for definition in load_seeds().ticket_types:
        wanted = {
            status["code"]: status["advance_rules"]
            for status in definition.get("statuses", [])
            if status.get("advance_rules")
        }
        if not wanted:
            continue
        record = await types.get_by_code(definition["code"])
        if record is None:
            continue
        step_codes = {step.code for step in record.steps}
        field_keys = {field.key for field in record.fields}
        current = sorted(record.statuses, key=lambda status: status.sort_order)
        rows = {status.code: _as_dict(status) for status in current}
        changed = False
        for code, rules in wanted.items():
            if code not in rows or rows[code]["advance_rules"]:
                continue
            if any(
                rule.get("step_code") and rule["step_code"] not in step_codes for rule in rules
            ):
                continue
            if any(rule.get("field_key") and rule["field_key"] not in field_keys for rule in rules):
                continue
            rows[code]["advance_rules"] = rules
            changed = True
            report.add(f"{definition['code']}_{code}_rule")
        if changed:
            await types.replace_collections(
                record,
                None,
                normalize_statuses([rows[status.code] for status in current]),
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
                entity_id="status-advance-rules",
                entity_label="Reglas de estado",
                summary="Asignó las condiciones de los estados que aún no tenían",
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
    print(f"status rules: {result.created or 'already applied'}")
