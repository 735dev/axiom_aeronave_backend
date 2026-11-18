"""Carga el papel de Transvalor del 17-11-2026 en una base que ya existe.

`python -m app.scripts.apply_tvo_17nov`. Se puede repetir.

1. Ficha real: YV2822 (King Air 300) y YV3143 (PA-31T), las dos en RAV 91.
2. Lista OACI del papel, con alcance nacional o internacional.
3. FBO Caracas Flight Service, sin precio.
4. Formulario de tierra: operador del FBO, boleta, observaciones y el texto del remolque.

Apaga las matrículas y los aeropuertos de demostración que el papel no trae.
No crea usuarios de tripulación: el papel no trae correos. Los nombres quedan en la ficha.
No carga brokers, tabulador ni conceptos de gasto.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.fixtures.tvo_17nov import (
    AIRPORTS,
    FBO,
    FLEET,
    MODELS,
    OPERATION_KINDS,
    RETIRED_AIRPORTS,
    RETIRED_MODELS,
    RETIRED_TAILS,
    patch_operation_fields,
    sheet_extra,
)
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.catalog import CatalogItemRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.scripts.apply_aircraft_catalogs import _as_dict
from app.scripts.init import SeedReport
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.type_schema import normalize_fields

logger = logging.getLogger(__name__)

OPERATION_TYPE = "operation"


def _same_sheet(raw: str | None, wanted: str) -> bool:
    if not raw:
        return False
    try:
        current = json.loads(raw)
        target = json.loads(wanted)
    except json.JSONDecodeError:
        return False
    return isinstance(current, dict) and current == target


async def _upsert(repo: CatalogItemRepository, catalog: str, item: dict[str, Any], report: SeedReport) -> None:
    payload = {
        "name": item["name"],
        "extra": item.get("extra"),
        "sort_order": item.get("sort_order", 0),
        "is_active": True,
    }
    current = await repo.get_by_catalog_code(catalog, item["code"])
    if current is None:
        await repo.create(
            {
                "catalog": catalog,
                "code": item["code"],
                "is_system": False,
                **payload,
            }
        )
        report.add(catalog)
        return
    changed = any(getattr(current, key) != value for key, value in payload.items())
    if not changed:
        return
    await repo.update(current.id, payload)
    report.add(f"{catalog}_updated")


async def _retire(repo: CatalogItemRepository, catalog: str, codes: tuple[str, ...], report: SeedReport) -> None:
    for code in codes:
        current = await repo.get_by_catalog_code(catalog, code)
        if current is None or not current.is_active:
            continue
        await repo.update(current.id, {"is_active": False})
        report.add(f"{catalog}_retired")


async def _retire_other_kinds(repo: CatalogItemRepository, report: SeedReport) -> None:
    keep = {item["code"] for item in OPERATION_KINDS}
    for code, row in (await repo.items_by_code("operation_kinds")).items():
        if code in keep or not row.is_active:
            continue
        await repo.update(row.id, {"is_active": False})
        report.add("operation_kinds_retired")


async def _fleet(session: AsyncSession, report: SeedReport) -> None:
    repo = CatalogItemRepository(session)
    for model in MODELS:
        await _upsert(repo, "aircraft_models", model, report)
    for aircraft in FLEET:
        await _upsert(
            repo,
            "aircrafts",
            {
                "code": aircraft["code"],
                "name": aircraft["name"],
                "extra": aircraft["extra"],
                "sort_order": aircraft["sort_order"],
            },
            report,
        )
        extra = sheet_extra(aircraft)
        sheet = await repo.get_by_catalog_code("aircraft_sheets", aircraft["code"])
        if sheet is None:
            await repo.create(
                {
                    "catalog": "aircraft_sheets",
                    "code": aircraft["code"],
                    "name": aircraft["code"],
                    "extra": extra,
                    "is_system": False,
                    "is_active": True,
                    "sort_order": aircraft["sort_order"],
                }
            )
            report.add("aircraft_sheets")
        elif not _same_sheet(sheet.extra, extra) or sheet.name != aircraft["code"] or not sheet.is_active:
            await repo.update(
                sheet.id,
                {"name": aircraft["code"], "extra": extra, "is_active": True, "sort_order": aircraft["sort_order"]},
            )
            report.add("aircraft_sheets_updated")
    await _retire(repo, "aircrafts", RETIRED_TAILS, report)
    await _retire(repo, "aircraft_sheets", RETIRED_TAILS, report)
    await _retire(repo, "aircraft_models", RETIRED_MODELS, report)


async def _airports(session: AsyncSession, report: SeedReport) -> None:
    repo = CatalogItemRepository(session)
    for airport in AIRPORTS:
        await _upsert(repo, "airports", airport, report)
    await _retire(repo, "airports", RETIRED_AIRPORTS, report)


async def _catalogs(session: AsyncSession, report: SeedReport) -> None:
    repo = CatalogItemRepository(session)
    for kind in OPERATION_KINDS:
        await _upsert(repo, "operation_kinds", kind, report)
    await _retire_other_kinds(repo, report)
    await _upsert(repo, "fbos", FBO, report)
    others = tuple(
        code for code in (await repo.items_by_code("fbos")) if code != FBO["code"]
    )
    await _retire(repo, "fbos", others, report)


async def _form(session: AsyncSession, report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    record = await types.get_by_code(OPERATION_TYPE)
    if record is None:
        return
    current = sorted(record.fields, key=lambda field: field.sort_order)
    rows = [_as_dict(field) for field in current]
    desired = normalize_fields(patch_operation_fields(rows))
    if normalize_fields(rows) == desired:
        return
    await types.replace_collections(record, desired, None, None)
    report.add("operation_form")


async def run_upgrade(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    with without_audit():
        await _fleet(session, report)
        await _airports(session, report)
        await _catalogs(session, report)
        await _form(session, report)
    if report.total:
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(report.created.items()))
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.CATALOG,
                entity_id="tvo-17nov",
                entity_label="Papel del 17-11-2026",
                summary=f"Ficha, aeropuertos, FBO y formato de tierra: {detail}",
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
    logger.info("tvo 17-11: %s", result.created)
