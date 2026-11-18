"""Idempotent upgrade of an EXISTING database to the 2026-11-16 operations meeting.

`python -m app.scripts.apply_reunion_aerea`. The seed never overwrites a ticket type or a
catalog row that already exists. Run once per database (harmless to repeat):

1. permissions for the instruction, the crew, ground and the passenger desk, granted on
   top of what each role already has (nothing is revoked);
2. catalogs `flight_regimes` and `aircraft_sheets`, the extra aircraft conditions, EUR,
   and domestic/international on the airports already loaded (Maiquetía becomes SVMI
   only when that code is still free);
3. the operation form: new fields, `when:` visibility, steps, and the removal of
   `overnight`, `exchange_rate` and `clearance` from the form (historical payload keys stay);
4. payloads where `service_condition` was one option and where `overnight` was checked.

`aircrafts.extra` is the model code and is not touched. No fuel supplier or FBO is seeded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import database
from app.config.audit import AuditAction, AuditEntity
from app.models.ticket import BusinessRule, Ticket, TicketType
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.catalog import CatalogItemRepository
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.scripts.apply_aircraft_catalogs import _as_dict
from app.scripts.init import SeedReport, _seed_permissions
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.seed_loader import load_seeds
from app.utils.type_schema import normalize_fields, normalize_steps

logger = logging.getLogger(__name__)

OPERATION_TYPE = "operation"
INSTRUCTION = "operations.instruction"
PILOT_STEP = "requirements"
GROUND_STEP = "ground"
AVSEC_STEP = "avsec"
REMOVED = frozenset({"overnight", "exchange_rate", "clearance"})
LEAVE = frozenset(
    {
        "aircraft_reported",
        "aircraft_availability",
        "ata_failure_code",
        "failure_description",
        "corrective_measure",
    }
)
SYSTEM_READONLY = frozenset({"control_number", "scope"})
PILOT_FIELDS = frozenset(
    {
        "flight_time",
        "alternate",
        "captain",
        "copilot",
        "dispatcher",
        "schedule",
        "flight_rules",
        "governing_rav",
        "actual_departure",
        "route_note",
        "fbo_services",
    }
)
GROUND_FIELDS = frozenset(
    {
        "ground_support_services",
        "tow_count",
        "foreign_support_services",
        "fuel_supplier",
        "fuel_quantity",
        "tow_lines",
        "fuel_requested",
        "fuel_slip",
        "fuel_slip_file",
    }
)
CONDITION_VALUES = frozenset({"round_trip", "drop_off", "pick_up", "stopover", "overnight"})
SERVICE_CONDITION_OPTIONS = [
    {"value": "round_trip", "label": "Ida y vuelta"},
    {"value": "drop_off", "label": "Solo dejar"},
    {"value": "pick_up", "label": "Solo buscar"},
    {"value": "stopover", "label": "Escala"},
    {"value": "overnight", "label": "Pernocta"},
]
TAILS = ("YV2323", "YV2528", "YV2822")
SHEET_EXTRA = {
    "regime": "rav_91",
    "serial": "",
    "year": "",
    "pax": "",
    "mtow": "",
    "cargo": "",
    "status": "operational",
    "expected_return": "",
}
SHEET_PRESERVE = ("serial", "year", "pax", "mtow", "cargo")
DOMESTIC_AIRPORTS = ("SVMI", "CCS", "PZO", "SVBC", "SVSA")
INTERNATIONAL_AIRPORTS = ("MDPC",)
GRANTS = {
    "operations": ("operations.instruction",),
    "pilot": ("operations.crew", "operations.ground"),
    "ground": ("operations.ground",),
    "avsec": ("operations.avsec",),
}
ORDER = (
    "date",
    "control_number",
    "aircraft",
    "aircraft_brand",
    "aircraft_model",
    "regime",
    "kind",
    "service_type",
    "service_condition",
    "origin",
    "destination",
    "departure_time",
    "return_time",
    "passengers",
    "cargo_kind",
    "stops",
    "scope",
    "catering",
    "catering_detail",
    "dangerous_goods",
    "dangerous_goods_file",
    "person_flags",
    "fbo_processed",
    "fbo_name",
    "manifest",
    "passport",
    "observations",
    "flight_time",
    "alternate",
    "captain",
    "copilot",
    "dispatcher",
    "schedule",
    "flight_rules",
    "governing_rav",
    "actual_departure",
    "route_note",
    "fbo_services",
    "ground_support_services",
    "tow_count",
    "tow_lines",
    "foreign_support_services",
    "fuel_requested",
    "fuel_supplier",
    "fuel_quantity",
    "fuel_slip",
    "fuel_slip_file",
    "avsec_note",
    "aircraft_reported",
    "aircraft_availability",
    "ata_failure_code",
    "failure_description",
    "corrective_measure",
)


def sheet_json() -> str:
    return json.dumps(SHEET_EXTRA, ensure_ascii=False, separators=(",", ":"))


def _field(**values: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "required": False,
        "width": "half",
        "help": None,
        "options": None,
        "catalog": None,
        "read_only": False,
        "role": None,
        "depends_on": None,
        "view_permission": None,
        "edit_permission": None,
        "step_code": None,
    }
    row.update(values)
    return row


def _new_fields() -> dict[str, dict[str, Any]]:
    return {
        "regime": _field(key="regime", label="Régimen", type="select", catalog="flight_regimes"),
        "cargo_kind": _field(
            key="cargo_kind",
            label="Qué se transporta",
            type="checkbox_group",
            width="full",
            options=[
                {"value": "people", "label": "Personas"},
                {"value": "things", "label": "Cosas"},
            ],
        ),
        "stops": _field(
            key="stops",
            label="Escalas",
            type="textarea",
            width="full",
            help="Una escala por línea: código OACI y hora estimada. Origen y destino siguen arriba.",
        ),
        "catering": _field(key="catering", label="¿Catering o pedido especial?", type="checkbox"),
        "catering_detail": _field(
            key="catering_detail",
            label="Detalle del pedido",
            type="textarea",
            width="full",
            depends_on="when:catering",
        ),
        "dangerous_goods_file": _field(
            key="dangerous_goods_file",
            label="Autorización de mercancías peligrosas",
            type="file",
            width="full",
            depends_on="when:dangerous_goods",
        ),
        "person_flags": _field(
            key="person_flags",
            label="Personas a bordo",
            type="checkbox_group",
            width="full",
            options=[
                {"value": "minor", "label": "Menor"},
                {"value": "disability", "label": "Discapacidad"},
                {"value": "wheelchair", "label": "Silla de ruedas"},
                {"value": "minor_permit", "label": "Permiso de menor"},
            ],
        ),
        "actual_departure": _field(
            key="actual_departure",
            label="Hora real de salida",
            type="time",
            step_code=PILOT_STEP,
        ),
        "route_note": _field(
            key="route_note",
            label="Ruta real si cambió",
            type="textarea",
            width="full",
            step_code=PILOT_STEP,
        ),
        "tow_lines": _field(
            key="tow_lines",
            label="Remolques",
            type="textarea",
            width="full",
            help="Un remolque por línea: desde, hasta. El monto se carga en el gasto, no hay tarifa de catálogo.",
            step_code=GROUND_STEP,
        ),
        "fuel_requested": _field(
            key="fuel_requested",
            label="¿Combustible?",
            type="checkbox",
            step_code=GROUND_STEP,
        ),
        "fuel_slip": _field(
            key="fuel_slip",
            label="Número de vale",
            type="text",
            depends_on="when:fuel_requested",
            step_code=GROUND_STEP,
        ),
        "fuel_slip_file": _field(
            key="fuel_slip_file",
            label="Vale de combustible",
            type="file",
            width="full",
            depends_on="when:fuel_requested",
            step_code=GROUND_STEP,
        ),
        "avsec_note": _field(
            key="avsec_note",
            label="Supervisión de la estación de personas",
            type="textarea",
            width="full",
            depends_on="when:fbo_processed",
            step_code=AVSEC_STEP,
        ),
    }


PATCHES: dict[str, dict[str, Any]] = {
    "service_condition": {
        "type": "checkbox_group",
        "options": SERVICE_CONDITION_OPTIONS,
        "catalog": None,
        "required": False,
    },
    "departure_time": {"label": "Hora estimada de salida", "required": True},
    "passengers": {
        "label": "Personas",
        "help": "Personas a bordo, sin la tripulación.",
    },
    "scope": {
        "read_only": True,
        "required": False,
        "help": "Lo calcula el sistema: si algún punto es internacional, la operación es internacional.",
    },
    "aircraft_brand": {
        "read_only": True,
        "required": False,
        "edit_permission": None,
        "help": "Sale de la matrícula. No se vuelve a escribir.",
    },
    "aircraft_model": {
        "read_only": True,
        "required": False,
        "edit_permission": None,
        "depends_on": "aircraft_brand",
        "help": "Sale de la matrícula. No se vuelve a escribir.",
    },
    "dangerous_goods": {
        "help": "Sí o no. Si es sí, adjunta la autorización firmada. No hace falta el inventario.",
    },
    "fbo_name": {"depends_on": "when:fbo_processed"},
    "foreign_support_services": {"depends_on": "when:fbo_processed"},
    "fuel_supplier": {"depends_on": "when:fuel_requested"},
    "fuel_quantity": {
        "depends_on": "when:fuel_requested",
        "label": "Litros",
        "help": "Litros. El monto lo manda el broker después; no lo escribas aquí.",
    },
}


def upgrade_operation_fields(rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The operation fields this meeting owns. Keys not listed keep their other attributes."""
    original = [key for key in rows if key not in REMOVED]
    fields = {key: dict(value) for key, value in rows.items() if key not in REMOVED}
    for key, patch in PATCHES.items():
        if key in fields:
            fields[key].update(patch)
    for key, spec in _new_fields().items():
        if key not in fields:
            fields[key] = spec
        else:
            fields[key].update(spec)
    for key in PILOT_FIELDS:
        if key in fields:
            fields[key]["step_code"] = PILOT_STEP
    for key in GROUND_FIELDS:
        if key in fields:
            fields[key]["step_code"] = GROUND_STEP
    if "fbo_services" in fields:
        fields["fbo_services"]["depends_on"] = "when:fbo_processed"
    for key, row in fields.items():
        if row.get("step_code") or key in LEAVE:
            continue
        if key in SYSTEM_READONLY:
            row["edit_permission"] = None
            continue
        if row.get("read_only"):
            continue
        row["edit_permission"] = INSTRUCTION
    order = [key for key in ORDER if key in fields]
    order.extend(key for key in original if key in fields and key not in order)
    return [fields[key] for key in order]


def migrate_operation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a single condition and keep a checked overnight. Historical keys stay."""
    data = dict(payload)
    condition = data.get("service_condition")
    if isinstance(condition, str) and condition in CONDITION_VALUES:
        condition = [condition]
        data["service_condition"] = condition
    if data.get("overnight") is True:
        if isinstance(condition, list):
            if "overnight" not in condition:
                data["service_condition"] = [*condition, "overnight"]
        elif condition is None or condition == "":
            data["service_condition"] = ["overnight"]
    return data


def _sheet_data(raw: str | None) -> dict[str, Any] | None:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _seed_missing(
    session: AsyncSession, catalog: str, items: list[dict], report: SeedReport
) -> None:
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


async def _aircraft_sheets(session: AsyncSession, report: SeedReport) -> None:
    repo = CatalogItemRepository(session)
    for index, code in enumerate(TAILS, start=1):
        current = await repo.get_by_catalog_code("aircraft_sheets", code)
        if current is None:
            await repo.create(
                {
                    "catalog": "aircraft_sheets",
                    "code": code,
                    "name": code,
                    "extra": sheet_json(),
                    "is_system": False,
                    "is_active": True,
                    "sort_order": index,
                }
            )
            report.add("aircraft_sheets")
            continue
        data = _sheet_data(current.extra)
        if data is None or str(data.get("regime") or "").strip():
            continue
        data["regime"] = "rav_91"
        for key in SHEET_PRESERVE:
            if key in data and str(data[key] or "").strip():
                continue
        current.extra = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        report.add("aircraft_sheet_regime")


async def _airports(session: AsyncSession, report: SeedReport) -> bool:
    """Set domestic/international. Rename CCS to SVMI only when SVMI does not exist."""
    repo = CatalogItemRepository(session)
    renamed = False
    svmi = await repo.get_by_catalog_code("airports", "SVMI")
    ccs = await repo.get_by_catalog_code("airports", "CCS")
    if svmi is None and ccs is not None:
        ccs.code = "SVMI"
        renamed = True
        report.add("airport_svmi")
        await session.flush()
    for code in DOMESTIC_AIRPORTS:
        row = await repo.get_by_catalog_code("airports", code)
        if row is not None and row.extra != "domestic":
            row.extra = "domestic"
            report.add("airport_extra")
    for code in INTERNATIONAL_AIRPORTS:
        row = await repo.get_by_catalog_code("airports", code)
        if row is not None and row.extra != "international":
            row.extra = "international"
            report.add("airport_extra")
    return renamed


def _retarget_route_key(key: str) -> str:
    return "-".join("SVMI" if part == "CCS" else part for part in str(key).split("-"))


async def _retarget_route_limits(session: AsyncSession, report: SeedReport) -> None:
    rules = (await session.execute(select(BusinessRule))).scalars().all()
    for rule in rules:
        limits = rule.route_limits
        if not limits:
            continue
        moved = {_retarget_route_key(key): value for key, value in limits.items()}
        if moved == limits:
            continue
        rule.route_limits = moved
        flag_modified(rule, "route_limits")
        report.add("route_limits")


async def _grant(session: AsyncSession, report: SeedReport) -> None:
    await _seed_permissions(session, load_seeds(), report)
    permissions = PermissionRepository(session)
    roles = RoleRepository(session)
    for role_code, codes in GRANTS.items():
        role = await roles.get_by_code(role_code)
        if role is None:
            continue
        held = {item.code for item in role.permissions}
        missing = [code for code in codes if code not in held]
        if not missing:
            continue
        found = await permissions.by_codes(missing)
        if len(found) != len(missing):
            raise RuntimeError(f"Role {role_code} is missing permissions: {missing}")
        await roles.replace_permissions(
            role, [*role.permissions, *[found[code] for code in missing]]
        )
        report.add("role_grants")
    added = await permissions.sync_superuser_roles()
    if added:
        report.add("superuser_links", added)


def _step_row(step: Any) -> dict[str, Any]:
    return {
        "code": step.code,
        "name": step.name,
        "role_id": step.role_id,
        "responsible_area": step.responsible_area,
        "required": step.required,
        "requires_signature": step.requires_signature,
    }


async def _operation_form(session: AsyncSession, report: SeedReport) -> None:
    types = TicketTypeRepository(session)
    record = await types.get_by_code(OPERATION_TYPE)
    if record is None:
        return
    current = sorted(record.fields, key=lambda field: field.sort_order)
    rows = {field.key: _as_dict(field) for field in current}
    desired_fields = normalize_fields(upgrade_operation_fields(rows))
    fields_changed = normalize_fields([_as_dict(field) for field in current]) != desired_fields

    steps = [_step_row(step) for step in sorted(record.steps, key=lambda step: step.sort_order)]
    steps_changed = False
    if not any(step["code"] == AVSEC_STEP for step in steps):
        role = await RoleRepository(session).get_by_code(AVSEC_STEP)
        if role is None:
            raise RuntimeError("Role avsec is missing")
        insert_at = next(
            (index for index, step in enumerate(steps) if step["code"] == "ground"), len(steps) - 1
        )
        steps.insert(
            insert_at + 1,
            {
                "code": AVSEC_STEP,
                "name": "Estación de personas",
                "role_id": role.id,
                "responsible_area": None,
                "required": False,
                "requires_signature": False,
            },
        )
        steps_changed = True
    desired_steps = normalize_steps(steps) if steps_changed else None
    if not fields_changed and not steps_changed:
        return
    await types.replace_collections(
        record,
        desired_fields if fields_changed else None,
        None,
        desired_steps,
    )
    report.add("operation_form")


async def _payloads(session: AsyncSession, report: SeedReport) -> None:
    type_id = (
        await session.execute(select(TicketType.id).where(TicketType.code == OPERATION_TYPE))
    ).scalar_one_or_none()
    if type_id is None:
        return
    tickets = (
        (await session.execute(select(Ticket).where(Ticket.type_id == type_id))).scalars().all()
    )
    for ticket in tickets:
        current = dict(ticket.payload or {})
        migrated = migrate_operation_payload(current)
        if migrated == current:
            continue
        ticket.payload = migrated
        flag_modified(ticket, "payload")
        report.add("operation_payloads")


async def run_upgrade(session: AsyncSession) -> SeedReport:
    data = load_seeds()
    report = SeedReport()
    with without_audit():
        await _grant(session, report)
        await _seed_missing(
            session, "flight_regimes", data.catalogs.get("flight_regimes", []), report
        )
        await _seed_missing(
            session, "aircraft_statuses", data.catalogs.get("aircraft_statuses", []), report
        )
        await _seed_missing(session, "currencies", data.catalogs.get("currencies", []), report)
        await _aircraft_sheets(session, report)
        if await _airports(session, report):
            await _retarget_route_limits(session, report)
        await _operation_form(session, report)
        await _payloads(session, report)
    if report.total:
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(report.created.items()))
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.UPDATE,
                entity=AuditEntity.TICKET_TYPE,
                entity_id="reunion-aerea",
                entity_label="Reunión de operaciones",
                summary=f"Formulario, catálogos y permisos de la reunión aérea: {detail}",
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
    print(f"reunión aérea: {result.created or 'already applied'}")
