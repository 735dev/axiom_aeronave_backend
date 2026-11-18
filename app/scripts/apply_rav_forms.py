"""Idempotent upgrade: operation, report and hangar forms follow RAV 119, 135 and 111.

`python -m app.scripts.apply_rav_forms`. The seed never overwrites a type that already
exists, so a database created before this change keeps the old forms. Run once per
database (harmless to repeat):

1. loads the RAV service classes into the `service_types` catalog when the code is missing;
2. appends the regulatory fields that the seed defines and the form does not have yet;
3. fills the help text of passengers and fuel only where that help is still empty;
4. adds the two operation rules (19 seats, dangerous goods) when the name is missing.

Tickets already saved are not rewritten.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.business_rule import BusinessRuleRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.scripts.apply_aircraft_catalogs import FIELD_COLUMNS, _as_dict, _seed_items
from app.scripts.init import SeedReport
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.seed_loader import load_seeds
from app.utils.type_schema import normalize_fields

logger = logging.getLogger(__name__)

#: Fields this upgrade may append, in seed order. Anything else on the form stays.
EXTRA_KEYS = {
    "operation": (
        "schedule",
        "flight_rules",
        "governing_rav",
        "alternate",
        "copilot",
        "dangerous_goods",
    ),
    "technical_report": ("airworthiness_condition",),
    "hangar_incident": ("area", "equipment"),
}
HELP = {
    "operation": {
        "passengers": (
            "Asientos de pasajeros, sin la tripulación. La RAV 135 llega hasta 19 (sección 119.10)."
        ),
        "fuel_quantity": "Cantidad a bordo. El combustible mínimo del vuelo lo fija la RAV 91.",
    }
}
RAV_RULES = (
    "Más de 19 pasajeros sale de la RAV 135",
    "Mercancías peligrosas exigen aprobación",
)


def _row_from_seed(item: dict[str, Any]) -> dict[str, Any]:
    return {column: item.get(column) for column in FIELD_COLUMNS}


async def _append_fields(session: AsyncSession, seeds: list[dict], report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    by_code = {item["code"]: item for item in seeds}
    for code, keys in EXTRA_KEYS.items():
        record = await types.get_by_code(code)
        seed = by_code.get(code)
        if record is None or seed is None:
            continue
        current = sorted(record.fields, key=lambda f: f.sort_order)
        rows = {f.key: _as_dict(f) for f in current}
        order = [f.key for f in current]
        added = False
        for key in keys:
            if key in rows:
                continue
            source = next((f for f in seed["fields"] if f["key"] == key), None)
            if source is None:
                continue
            rows[key] = _row_from_seed(source)
            order.append(key)
            added = True
            report.add(f"{code}_fields")
        for key, help_text in HELP.get(code, {}).items():
            if key in rows and not (rows[key].get("help") or "").strip():
                rows[key]["help"] = help_text
                added = True
                report.add(f"{code}_help")
        if not added:
            continue
        await types.replace_collections(
            record, normalize_fields([rows[key] for key in order]), None, None
        )


async def _add_rules(session: AsyncSession, rules: list[dict], report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    repo = BusinessRuleRepository(session)
    wanted = {name: rule for name, rule in ((r["name"], r) for r in rules) if name in RAV_RULES}
    for name, seed in wanted.items():
        ticket_type = await types.get_by_code(seed["type_code"])
        if ticket_type is None or await repo.exists(ticket_type.id, name):
            continue
        await repo.create(
            {
                "type_id": ticket_type.id,
                "name": name,
                "field": seed["field"],
                "operator": seed["operator"],
                "value": seed.get("value"),
                "action": seed["action"],
                "message": seed["message"],
                "route_limits": seed.get("route_limits"),
                "is_active": True,
            }
        )
        report.add("rav_rules")


async def run_upgrade(session: AsyncSession) -> SeedReport:
    data = load_seeds()
    report = SeedReport()
    with without_audit():
        await _seed_items(session, "service_types", data.catalogs["service_types"], report)
        await _append_fields(session, data.ticket_types, report)
        await _add_rules(session, data.business_rules, report)
    if report.total:
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(report.created.items()))
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.TICKET_TYPE,
                entity_id="rav-forms",
                entity_label="Formularios según las RAV",
                summary=f"Datos y reglas de las RAV 119, 135 y 111: {detail}",
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
    print(f"rav forms: {result.created or 'already applied'}")
