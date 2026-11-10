"""Marca, modelo y matrícula de una operación deben ser coherentes entre sí.

Pure check: the service passes in the payload, the keys that changed in this write and the
active catalog data (`aircraft_models.extra` = brand code, `aircrafts.extra` = model code).
Only pairs that involve a key written now are checked, and only when both values are known
catalog codes: an old ticket that still carries free text stays editable on other fields.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

MODEL_NOT_BRAND = "El modelo no corresponde a la marca."
BRAND_NOT_MODEL = "La marca no corresponde al modelo."
AIRCRAFT_NOT_MODEL = "La matrícula no corresponde a ese modelo."
MODEL_NOT_AIRCRAFT = "El modelo no corresponde a la matrícula."


@dataclass(frozen=True)
class AircraftKeys:
    """Form keys of the fields that carry each role (None when the type has no such field)."""

    brand: str
    model: str
    aircraft: str | None


def aircraft_coherence_errors(
    keys: AircraftKeys,
    payload: Mapping[str, Any],
    changed: Collection[str],
    *,
    brands: Collection[str],
    models: Mapping[str, str | None],
    fleet: Mapping[str, str | None],
) -> dict[str, str]:
    brand = payload.get(keys.brand)
    model = payload.get(keys.model)
    aircraft = payload.get(keys.aircraft) if keys.aircraft else None
    errors: dict[str, str] = {}

    if (
        {keys.brand, keys.model} & set(changed)
        and brand in brands
        and model in models
        and models[model] != brand
    ):
        if keys.model in changed:
            errors[keys.model] = MODEL_NOT_BRAND
        else:
            errors[keys.brand] = BRAND_NOT_MODEL

    if (
        keys.aircraft
        and {keys.aircraft, keys.model} & set(changed)
        and model in models
        and aircraft in fleet
        and fleet[aircraft] != model
        and keys.model not in errors
    ):
        if keys.aircraft in changed:
            errors[keys.aircraft] = AIRCRAFT_NOT_MODEL
        else:
            errors[keys.model] = MODEL_NOT_AIRCRAFT
    return errors
