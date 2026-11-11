"""Idempotent upgrade of an EXISTING database: passenger count, exchange rate and people lists.

`python -m app.scripts.apply_operation_form_fixes`. The seed never overwrites what a database
already holds, so one created before this change keeps the old definitions. Run once per
database (harmless to repeat). On the operation type:

1. `passengers` becomes a whole-number field (`integer`): never negative, never a fraction;
2. `exchange_rate` gets the role `exchange_rate`, so the form can propose the official rate of
   the day, and a help text that says the person may type it by hand;
3. `captain` becomes a dropdown of the active users with the role `pilot`; `fbo_name` and
   `fuel_supplier` become dropdowns over the new catalogs `fbos` and `fuel_suppliers` (empty:
   Transvalor loads them). A value already saved as free text stays readable and editable.

Every other field is left as is. Tickets already created are not touched.
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
from app.scripts.apply_aircraft_catalogs import _as_dict
from app.scripts.init import SeedReport
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.type_schema import normalize_fields

logger = logging.getLogger(__name__)

OPERATION_TYPE = "operation"
HELP = "La propone el sistema (tasa oficial). Puedes escribirla a mano."
WANTED: dict[str, dict[str, Any]] = {
    "passengers": {"type": "integer"},
    "exchange_rate": {"role": "exchange_rate", "help": HELP},
    "captain": {"type": "select", "catalog": "users:pilot", "options": None},
    "fbo_name": {"type": "select", "catalog": "fbos", "options": None},
    "fuel_supplier": {"type": "select", "catalog": "fuel_suppliers", "options": None},
}


async def _upgrade_operation(session: AsyncSession, report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    record = await types.get_by_code(OPERATION_TYPE)
    if record is None:
        return
    current = sorted(record.fields, key=lambda f: f.sort_order)
    rows = {f.key: _as_dict(f) for f in current}
    changed = False
    for key, wanted in WANTED.items():
        if key not in rows:
            continue
        if all(rows[key][column] == value for column, value in wanted.items()):
            continue
        rows[key].update(wanted)
        changed = True
        report.add(f"operation_{key}_field")
    if changed:
        await types.replace_collections(
            record, normalize_fields([rows[f.key] for f in current]), None, None
        )


async def run_upgrade(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    with without_audit():
        await _upgrade_operation(session, report)
    if report.total:
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.TICKET_TYPE,
                entity_id="operation-form-fixes",
                entity_label="Pasajeros, tasa y listas de personas",
                summary="Pasajeros entero; tasa propuesta; capitán, FBO y combustible como listas",
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
    print(f"operation form fixes: {result.created or 'already applied'}")
