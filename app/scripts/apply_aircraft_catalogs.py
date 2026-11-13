"""Idempotent upgrade of an EXISTING database to brand/model dropdowns:
`python -m app.scripts.apply_aircraft_catalogs`.

The seed never overwrites what already exists, so a database created before this change
has Marca and Modelo as read-only text that nothing fills. Run once per database (harmless
to repeat):

1. creates the items of the catalogs `aircraft_brands` and `aircraft_models` that are missing;
2. sets `extra` (the model code) on the seeded aircrafts, only where it is still empty;
3. on the operation type, turns `aircraft_brand` and `aircraft_model` into catalog selects
   (model filtered by brand, both editable) and moves `aircraft`, brand and model together
   right after the control number, as in the paper form. Every other field is left as is.

Tickets already created are not touched: their old free-text brand/model still reads, and the
coherence check only looks at values written in a later edit.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.catalog import CatalogItemRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.scripts.init import SeedReport
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.seed_loader import load_seeds
from app.utils.type_schema import normalize_fields

logger = logging.getLogger(__name__)

OPERATION_TYPE = "operation"
FIELD_COLUMNS = (
    "key",
    "label",
    "type",
    "required",
    "width",
    "help",
    "options",
    "catalog",
    "read_only",
    "role",
    "depends_on",
    "view_permission",
    "edit_permission",
    "step_code",
)
#: The attributes this upgrade owns on the three aircraft fields.
MANAGED = {
    "aircraft_brand": {
        "type": "select",
        "catalog": "aircraft_brands",
        "role": "aircraft_brand",
        "depends_on": None,
        "read_only": False,
        "options": None,
    },
    "aircraft_model": {
        "type": "select",
        "catalog": "aircraft_models",
        "role": "aircraft_model",
        "depends_on": "aircraft_brand",
        "read_only": False,
        "options": None,
    },
}
AIRCRAFT_KEY = "aircraft"
GROUP = (AIRCRAFT_KEY, "aircraft_brand", "aircraft_model")
ANCHOR = "control_number"


async def _seed_items(session: AsyncSession, catalog: str, items: list[dict], report) -> None:
    repo = CatalogItemRepository(session)
    for item in items:
        if await repo.get_by_catalog_code(catalog, item["code"]):
            continue
        await repo.create(
            {
                "catalog": catalog,
                "code": item["code"],
                "name": item["name"],
                "extra": item.get("extra"),
                "is_system": item.get("is_system", False),
                "is_active": True,
                "sort_order": item.get("sort_order", 0),
            }
        )
        report.add("catalog_items")


async def _link_fleet(session: AsyncSession, seeded: list[dict], report: SeedReport) -> None:
    repo = CatalogItemRepository(session)
    for item in seeded:
        if not item.get("extra"):
            continue
        current = await repo.get_by_catalog_code("aircrafts", item["code"])
        if current is not None and not (current.extra or "").strip():
            await repo.update(current.id, {"extra": item["extra"]})
            report.add("aircraft_models_linked")


def _as_dict(field: Any) -> dict[str, Any]:
    return {column: getattr(field, column) for column in FIELD_COLUMNS}


async def _upgrade_operation(session: AsyncSession, report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    record = await types.get_by_code(OPERATION_TYPE)
    if record is None:
        return
    current = sorted(record.fields, key=lambda f: f.sort_order)
    rows = {f.key: _as_dict(f) for f in current}
    if not {"aircraft_brand", "aircraft_model"} <= rows.keys():
        return
    for key, wanted in MANAGED.items():
        rows[key].update(wanted)

    order = [f.key for f in current if f.key not in GROUP]
    at = order.index(ANCHOR) + 1 if ANCHOR in order else min(2, len(order))
    group = [k for k in GROUP if k in rows]
    order[at:at] = group
    desired = [rows[k] for k in order]

    unchanged = [f.key for f in current] == order and all(
        all(getattr(f, c) == rows[f.key][c] for c in FIELD_COLUMNS) for f in current
    )
    if unchanged:
        return
    await types.replace_collections(record, normalize_fields(desired), None, None)
    report.add("operation_aircraft_fields")


async def run_upgrade(session: AsyncSession) -> SeedReport:
    data = load_seeds()
    report = SeedReport()
    with without_audit():
        await _seed_items(session, "aircraft_brands", data.catalogs["aircraft_brands"], report)
        await _seed_items(session, "aircraft_models", data.catalogs["aircraft_models"], report)
        await _link_fleet(session, data.catalogs["aircrafts"], report)
        await _upgrade_operation(session, report)
    if report.total:
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(report.created.items()))
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.TICKET_TYPE,
                entity_id="aircraft-catalogs",
                entity_label="Marca y modelo de aeronave",
                summary=f"Marca y modelo como listas ligadas a la matrícula: {detail}",
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
    print(f"aircraft catalogs: {result.created or 'already applied'}")
