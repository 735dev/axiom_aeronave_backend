"""Server-side validation of a ticket payload against its type's fields.

The equivalent of the Zod schema the reference builds from the same definition: the
client form is a convenience, this is the check. Pure functions: the service passes in
the field definitions, the caller's permissions and the active codes of the catalogs the
selects point to.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.config.tickets import (
    CHECKBOX_GROUP_MAX,
    PAYLOAD_MAX_KEYS,
    TEXT_MAX,
    TEXTAREA_MAX,
    FieldType,
)
from app.utils.money import to_decimal
from app.config.tickets import AMOUNT_MAX

REQUIRED_MESSAGE = "Este campo es obligatorio"
READ_ONLY_MESSAGE = "Este campo lo asigna el sistema"
UNKNOWN_MESSAGE = "Este campo no existe en el formulario"
FILE_MESSAGE = "Los archivos se adjuntan al registro, no se escriben en el formulario"

_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")
_MIN_YEAR, _MAX_YEAR = 1900, 2200
_CLEARED = object()


@dataclass
class PayloadResult:
    payload: dict[str, Any] = field(default_factory=dict)
    #: Invalid values, by field key.
    errors: dict[str, str] = field(default_factory=dict)
    #: Keys the caller may not write (edit_permission), by field key.
    forbidden: dict[str, str] = field(default_factory=dict)
    #: Keys whose value changed (written and different from what was stored).
    changed: set[str] = field(default_factory=set)


def option_values(options: Iterable[Mapping[str, str]] | None) -> set[str]:
    return {str(o["value"]) for o in options or []}


def catalogs_needed(fields: Iterable[Any], keys: Iterable[str]) -> set[str]:
    """Catalog keys that must be loaded to validate the given payload keys."""
    wanted = set(keys)
    return {f.catalog for f in fields if f.key in wanted and f.type == "select" and f.catalog}


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _normalize(
    definition: Any, value: Any, catalogs: Mapping[str, set[str]]
) -> tuple[Any, str | None]:
    kind = definition.type
    if kind in (FieldType.TEXT, FieldType.TEXTAREA):
        if not isinstance(value, str):
            return None, "Debe ser texto"
        text = value.strip()
        limit = TEXT_MAX if kind == FieldType.TEXT else TEXTAREA_MAX
        if len(text) > limit:
            return None, f"Máximo {limit} caracteres"
        return text, None
    if kind in (FieldType.NUMBER, FieldType.MONEY):
        number = to_decimal(value)
        if number is None:
            return None, "Debe ser un número"
        if abs(number) > AMOUNT_MAX:
            return None, "El valor es demasiado alto"
        if kind == FieldType.MONEY and number < 0:
            return None, "El monto no puede ser negativo"
        if isinstance(value, float) or "." in str(value):
            return float(number), None
        return int(number), None
    if kind == FieldType.SELECT:
        if not isinstance(value, str) or not value.strip():
            return None, "Debe ser una de las opciones"
        chosen = value.strip()
        allowed = (
            catalogs.get(definition.catalog, set())
            if definition.catalog
            else option_values(definition.options)
        )
        if chosen not in allowed:
            return None, "Opción no válida"
        return chosen, None
    if kind == FieldType.DATE:
        if not isinstance(value, str):
            return None, "Fecha no válida (AAAA-MM-DD)"
        try:
            parsed = date.fromisoformat(value.strip())
        except ValueError:
            return None, "Fecha no válida (AAAA-MM-DD)"
        if not _MIN_YEAR <= parsed.year <= _MAX_YEAR or len(value.strip()) != 10:
            return None, "Fecha no válida (AAAA-MM-DD)"
        return parsed.isoformat(), None
    if kind == FieldType.TIME:
        if not isinstance(value, str) or not _TIME_PATTERN.match(value.strip()):
            return None, "Hora no válida (HH:MM)"
        return value.strip(), None
    if kind == FieldType.CHECKBOX:
        if not isinstance(value, bool):
            return None, "Debe ser verdadero o falso"
        return value, None
    if kind == FieldType.CHECKBOX_GROUP:
        if not isinstance(value, list) or len(value) > CHECKBOX_GROUP_MAX:
            return None, "Debe ser una lista de opciones"
        allowed = option_values(definition.options)
        picked: list[str] = []
        for item in value:
            if not isinstance(item, str) or item not in allowed:
                return None, "Opción no válida"
            if item not in picked:
                picked.append(item)
        return picked, None
    if kind == FieldType.FILE:
        return None, FILE_MESSAGE
    return None, "Tipo de campo no soportado"


def validate_payload(
    fields: Iterable[Any],
    incoming: Mapping[str, Any],
    *,
    existing: Mapping[str, Any] | None,
    creating: bool,
    held: frozenset[str],
    catalogs: Mapping[str, set[str]],
) -> PayloadResult:
    """Validate the keys the caller sent and merge them over `existing`.

    On creation every required field must be present. On update only the keys sent are
    checked (an old ticket missing a field added to the type later must stay editable),
    but a required field cannot be cleared. Writing a read-only field, or one whose
    `edit_permission` the caller lacks, is refused unless the value does not change.
    """
    definitions = {f.key: f for f in fields}
    base = dict(existing or {})
    result = PayloadResult(payload=base)

    if len(incoming) > PAYLOAD_MAX_KEYS:
        result.errors["payload"] = "El formulario trae demasiados campos"
        return result

    for key, value in incoming.items():
        definition = definitions.get(key)
        if definition is None:
            result.errors[key] = UNKNOWN_MESSAGE
            continue
        cleared = _is_blank(value) and definition.type != FieldType.CHECKBOX
        if cleared:
            if definition.read_only and not creating and key in base:
                result.errors[key] = READ_ONLY_MESSAGE
            elif definition.required and not definition.read_only:
                result.errors[key] = REQUIRED_MESSAGE
            elif key in base:
                if definition.edit_permission and definition.edit_permission not in held:
                    result.forbidden[key] = definition.label
                else:
                    base.pop(key)
                    result.changed.add(key)
            continue
        normalized, error = _normalize(definition, value, catalogs)
        if error:
            result.errors[key] = error
            continue
        if base.get(key) == normalized and key in base:
            continue
        if definition.read_only:
            result.errors[key] = READ_ONLY_MESSAGE
            continue
        if definition.edit_permission and definition.edit_permission not in held:
            result.forbidden[key] = definition.label
            continue
        base[key] = normalized
        result.changed.add(key)

    if creating:
        for definition in definitions.values():
            if definition.required and not definition.read_only and definition.key not in base:
                result.errors.setdefault(definition.key, REQUIRED_MESSAGE)
    return result


def hide_restricted(
    payload: Mapping[str, Any], fields: Iterable[Any], held: frozenset[str]
) -> dict[str, Any]:
    """The payload as a given caller may read it: fields behind `view_permission` are dropped."""
    hidden = {f.key for f in fields if f.view_permission and f.view_permission not in held}
    return {k: v for k, v in payload.items() if k not in hidden}


def missing_labels(errors: Mapping[str, str], labels: Mapping[str, str]) -> list[str]:
    return [labels.get(k, k) for k, m in errors.items() if m == REQUIRED_MESSAGE]
