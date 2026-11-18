"""Datos del papel de Transvalor del 17-11-2026.

La ficha, la lista OACI, el FBO y el formato de tierra. No hay precios,
contrato, brokers ni correos: eso sigue pendiente del cliente.
"""

from __future__ import annotations

import json
from typing import Any

# Matrículas de demostración. El script las apaga en una base que ya existe.
# El seed las conserva porque las pruebas crean operaciones con ellas.
RETIRED_TAILS = ("YV2323", "YV2528")
RETIRED_MODELS = ("king_air_200", "cheyenne")
# Aeropuertos de demostración que el papel no trae.
RETIRED_AIRPORTS = ("PZO", "SVSA", "CCS")

OPERATION_KINDS = (
    {"code": "people_transfer", "name": "Traslado de personas", "sort_order": 1},
    {"code": "check_flight", "name": "Vuelo de comprobación", "sort_order": 2},
    {"code": "om_a_transfer", "name": "Traslado a OMAC", "sort_order": 3},
    {"code": "fuel_only", "name": "Solo combustible", "sort_order": 4},
)

MODELS = (
    {
        "code": "king_air_300",
        "name": "King Air 300",
        "extra": "beechcraft",
        "sort_order": 3,
    },
    {
        "code": "pa_31t",
        "name": "PA-31T",
        "extra": "piper",
        "sort_order": 4,
    },
)

FLEET = (
    {
        "code": "YV2822",
        "name": "King Air 300",
        "extra": "king_air_300",
        "sort_order": 1,
        "serial": "FA122",
        "year": "1987",
        "pax": "8 + 1 en el compartimento de carga",
        "mtow": "14100 lb (6395 kg)",
        "cargo": "510 lb (232 kg)",
        "endurance": "5 h 30 min",
        "night_nav": "Instrumentación para vuelos IR",
        "captain": "Richard Ollarves",
        "copilot": "Carlos Romero",
        "certification": "RAV 91, en proceso para RAV 135",
    },
    {
        "code": "YV3143",
        "name": "PA-31T",
        "extra": "pa_31t",
        "sort_order": 2,
        "serial": "31T-8020053",
        "year": "1980",
        "pax": "6 + 2 de los pilotos",
        "mtow": "9000 lb (4082 kg)",
        "cargo": "350 lb",
        "endurance": "4 h 30 min",
        "night_nav": "Instrumentación para vuelos full IFR",
        "captain": "Giorgio Ramones",
        "copilot": "Adriano Trevisi",
        "certification": "RAV 91",
    },
)

FBO = {
    "code": "caracas_flight_service",
    "name": "Caracas Flight Service, C.A.",
    "extra": "J-400878284",
    "sort_order": 1,
}

TOW_HELP = (
    "Un remolque por línea: fecha, operador, desde, hasta, costo, hielo y total. "
    "El formato del 17-11 anota el hielo en 5 USD. El costo espera el tabulador del proveedor."
)

# (code, name without the code prefix, scope). The visible name keeps the OACI code.
_AIRPORTS: tuple[tuple[str, str, str], ...] = (
    ("SVAC", "Acarigua — Oswaldo Guevara Mujica", "domestic"),
    ("SVAN", "Anaco", "domestic"),
    ("SVBC", "Barcelona — José Antonio Anzoátegui", "domestic"),
    ("SVBM", "Barquisimeto — Jacinto Lara", "domestic"),
    ("SVCP", "Carúpano — José Francisco Bermúdez", "domestic"),
    ("SVCS", "Caracas", "domestic"),
    ("SVCB", "Ciudad Bolívar — Tomás de Heres", "domestic"),
    ("SVPR", "Ciudad Guayana — Manuel Carlos Piar", "domestic"),
    ("SVCU", "Cumaná — Mariscal Antonio José de Sucre", "domestic"),
    ("SVVG", "El Vigía — Juan Pablo Pérez Alfonso", "domestic"),
    ("SVGI", "Güiria — Almirante Cristóbal Colón", "domestic"),
    ("SVLF", "La Fría", "domestic"),
    ("SVRS", "Los Roques — Gran Roque", "domestic"),
    ("SVMI", "Maiquetía — Simón Bolívar", "domestic"),
    ("SVBS", "Maracay — Florencio Gómez", "domestic"),
    ("SVMC", "Maracaibo — Rafael Urdaneta", "domestic"),
    ("SVMT", "Maturín — General José Tadeo Monagas", "domestic"),
    ("SVMG", "Margarita — Santiago Mariño", "domestic"),
    ("SVMD", "Mérida — Alberto Carnevalli", "domestic"),
    ("SVJC", "Paraguaná — Josefa Camejo", "domestic"),
    ("SVPA", "Puerto Ayacucho — Cacique Aramare", "domestic"),
    ("SVPC", "Puerto Cabello — General Bartolomé Salom", "domestic"),
    ("SVSR", "San Fernando de Apure", "domestic"),
    ("SVSO", "Santo Domingo del Táchira — Buenaventura Vivas", "domestic"),
    ("SVST", "San Tomé", "domestic"),
    ("SVSZ", "Santa Bárbara del Zulia — Miguel A. Urdaneta", "domestic"),
    ("SVTC", "Tucupita", "domestic"),
    ("SVVA", "Valencia — Arturo Michelena", "domestic"),
    ("SVVL", "Valera — Antonio Nicolás Briceño", "domestic"),
    ("TNCA", "Aruba — Reina Beatrix", "international"),
    ("TNCB", "Bonaire — Flamingo", "international"),
    ("TNCC", "Curazao — Hato", "international"),
    ("KFXE", "Fort Lauderdale Executive", "international"),
    ("SKBO", "Bogotá — El Dorado", "international"),
    ("SKCG", "Cartagena — Rafael Núñez", "international"),
    ("SEQM", "Quito — Mariscal Sucre", "international"),
    ("SPZO", "Cusco — Alejandro Velasco Astete", "international"),
    ("SBBV", "Boa Vista — Atlas Brasil Cantanhede", "international"),
    ("SBEG", "Manaus — Eduardo Gomes", "international"),
    ("MDPC", "Punta Cana", "international"),
    ("MPTO", "Panamá — Tocumen", "international"),
    ("MROC", "Alajuela — Juan Santamaría", "international"),
)

AIRPORTS: tuple[dict[str, Any], ...] = tuple(
    {
        "code": code,
        "name": f"{code} · {label}",
        "extra": scope,
        "sort_order": index,
    }
    for index, (code, label, scope) in enumerate(_AIRPORTS, start=1)
)


def sheet_extra(aircraft: dict[str, Any]) -> str:
    """Ficha operativa. El régimen queda en RAV 91 hasta que certifiquen la 135."""
    payload = {
        "regime": "rav_91",
        "serial": aircraft["serial"],
        "year": aircraft["year"],
        "pax": aircraft["pax"],
        "mtow": aircraft["mtow"],
        "cargo": aircraft["cargo"],
        "status": "operational",
        "expected_return": "",
        "endurance": aircraft["endurance"],
        "night_nav": aircraft["night_nav"],
        "captain": aircraft["captain"],
        "copilot": aircraft["copilot"],
        "certification": aircraft["certification"],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _insert(rows: list[dict[str, Any]], anchor: str, field: dict[str, Any]) -> None:
    current = next((row for row in rows if row["key"] == field["key"]), None)
    if current is not None:
        current.update(field)
        return
    index = next((i for i, row in enumerate(rows) if row["key"] == anchor), len(rows) - 1)
    rows.insert(index + 1, field)


def patch_operation_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ajusta el formulario de tierra al formato del 17-11. No inventa tarifas."""
    rows = [dict(field) for field in fields]
    by_key = {row["key"]: row for row in rows}
    if "tow_lines" in by_key:
        by_key["tow_lines"]["help"] = TOW_HELP
    if "fuel_slip" in by_key:
        by_key["fuel_slip"]["label"] = "Número de boleta"
    template = rows[0] if rows else {}
    _insert(
        rows,
        "fbo_name",
        _ground_field(
            template,
            key="fbo_operator",
            label="Nombre del operador de base fija",
            type="text",
            width="half",
            depends_on="when:fbo_processed",
        ),
    )
    _insert(
        rows,
        "fuel_slip",
        _ground_field(
            template,
            key="fuel_notes",
            label="Observaciones del combustible",
            type="textarea",
            width="full",
            depends_on="when:fuel_requested",
        ),
    )
    return rows


def _ground_field(template: dict[str, Any], **values: Any) -> dict[str, Any]:
    row = {key: template.get(key) for key in template}
    row.update(
        required=False,
        read_only=False,
        help=None,
        options=None,
        catalog=None,
        role=None,
        view_permission=None,
        edit_permission=None,
        step_code="ground",
    )
    row.update(values)
    return row
