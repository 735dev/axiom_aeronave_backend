"""Idempotent upgrade of an EXISTING database: the expense supplier becomes a catalog list.

`python -m app.scripts.apply_expense_supplier_catalog`. The seed never overwrites what a
database already holds, so one created before this change keeps Proveedor as free text.
Run once per database (harmless to repeat): on the expense type, `supplier` becomes a
select over the `suppliers` catalog. Every other field is left as is.

The catalog starts empty on purpose: the names belong to Transvalor and are loaded by the
superuser from the catalogs screen. Tickets already created keep the text they carry.
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
from app.scripts.apply_aircraft_catalogs import FIELD_COLUMNS, _as_dict
from app.scripts.init import SeedReport
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.type_schema import normalize_fields

logger = logging.getLogger(__name__)

EXPENSE_TYPE = "expense"
FIELD_KEY = "supplier"
WANTED: dict[str, Any] = {"type": "select", "catalog": "suppliers", "options": None}


async def _upgrade_expense(session: AsyncSession, report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    record = await types.get_by_code(EXPENSE_TYPE)
    if record is None:
        return
    current = sorted(record.fields, key=lambda f: f.sort_order)
    rows = {f.key: _as_dict(f) for f in current}
    if FIELD_KEY not in rows:
        return
    if all(rows[FIELD_KEY][column] == value for column, value in WANTED.items()):
        return
    rows[FIELD_KEY].update(WANTED)
    desired = [rows[f.key] for f in current]
    assert all(set(row) == set(FIELD_COLUMNS) for row in desired)
    await types.replace_collections(record, normalize_fields(desired), None, None)
    report.add("expense_supplier_field")


async def run_upgrade(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    with without_audit():
        await _upgrade_expense(session, report)
    if report.total:
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.TICKET_TYPE,
                entity_id="expense-supplier-catalog",
                entity_label="Proveedor del gasto",
                summary="Proveedor del gasto como lista de catálogo (antes texto libre)",
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
    print(f"expense supplier catalog: {result.created or 'already applied'}")
