"""Administrable catalogs. The keys are part of the API contract."""

from __future__ import annotations

import re
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
        "service_types", "Tipos de servicio", "Clase de servicio que se presta en la operación"
    ),
    CatalogDefinition(
        "aircraft_statuses", "Estados de aeronave", "Resultado posible de un reporte técnico"
    ),
    CatalogDefinition("aircraft_brands", "Marcas de aeronave", "Fabricantes de las aeronaves"),
    CatalogDefinition(
        "aircraft_models",
        "Modelos de aeronave",
        "Modelos por marca (el campo extra lleva el código de la marca)",
    ),
    CatalogDefinition(
        "aircrafts",
        "Aeronaves",
        "Flota registrada, con matrícula y modelo (el campo extra lleva el código del modelo)",
    ),
    CatalogDefinition("airports", "Aeropuertos", "Códigos y nombres usados en las rutas"),
    CatalogDefinition("fbos", "FBO", "Operadores de apoyo en tierra (FBO) con los que se trabaja"),
    CatalogDefinition(
        "fuel_suppliers",
        "Proveedores de combustible",
        "Brokers y proveedores de combustible de las operaciones",
    ),
    CatalogDefinition(
        "suppliers",
        "Proveedores",
        "Quienes emiten los comprobantes de un gasto",
    ),
    CatalogDefinition("locations", "Sedes", "Ubicaciones donde opera el personal"),
)
CATALOG_KEYS = frozenset(d.key for d in CATALOG_DEFINITIONS)

#: A select field may take its options from the active users of a role instead of a catalog:
#: `users:pilot`. The stored value is the person's full name as it was when the form was
#: filled (like the paper form), so renaming or deactivating a user never rewrites a flight.
USER_SOURCE_PREFIX = "users:"
USER_SOURCE_PATTERN = re.compile(r"^users:[a-z][a-z0-9_]{1,39}$")


def is_user_source(catalog: str | None) -> bool:
    return bool(catalog) and catalog.startswith(USER_SOURCE_PREFIX)


def user_source_role(catalog: str) -> str:
    return catalog[len(USER_SOURCE_PREFIX) :]


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
