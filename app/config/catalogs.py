"""Administrable catalogs. The keys are part of the API contract."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogDefinition:
    key: str
    name: str
    description: str


CATALOG_DEFINITIONS = (
    CatalogDefinition(
        "expense_concepts",
        "Conceptos de gasto",
        "Renglones que puede cargar cada rol en una operación",
    ),
    CatalogDefinition("units", "Unidades", "Unidades de medida para cantidades"),
    CatalogDefinition("currencies", "Monedas", "Monedas admitidas al registrar un gasto"),
    CatalogDefinition(
        "operation_kinds", "Tipos de operación", "Llevar y esperar, dejar e irse, u otros"
    ),
    CatalogDefinition("operation_scopes", "Alcances", "Nacional o internacional"),
    CatalogDefinition(
        "aircraft_statuses", "Estados de aeronave", "Resultado posible de un reporte técnico"
    ),
    CatalogDefinition("aircrafts", "Aeronaves", "Flota registrada, con matrícula y modelo"),
    CatalogDefinition("airports", "Aeropuertos", "Códigos y nombres usados en las rutas"),
    CatalogDefinition("locations", "Sedes", "Ubicaciones donde opera el personal"),
)
CATALOG_KEYS = frozenset(d.key for d in CATALOG_DEFINITIONS)

# Catalogs whose item code is stored by other tables. Deleting such an item is refused
# while a row still points at it. Later modules append their own (model path, column)
# pairs here; the repository resolves them lazily to avoid import cycles.
CATALOG_USAGE: dict[str, tuple[tuple[str, str], ...]] = {
    "aircrafts": (
        ("app.models.access.AppUser", "aircraft_id"),
        ("app.models.ticket.Ticket", "aircraft_code"),
    ),
    "locations": (("app.models.access.AppUser", "location"),),
}

CATALOG_ITEM_NAME_MIN = 2
CATALOG_ITEM_CODE_MAX = 60
