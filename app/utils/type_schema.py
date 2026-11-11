"""Structural validation of a ticket type's definition (fields, statuses, steps).

Raises `TicketError` with a Spanish message. Checks that need the database (roles,
permissions, other types) are done by the service on top of this.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.config.catalogs import CATALOG_KEYS, USER_SOURCE_PATTERN, is_user_source
from app.config.tickets import (
    KEY_PATTERN,
    MAX_FIELDS,
    MAX_OPTIONS,
    MAX_STATUSES,
    MAX_STEPS,
    ROLE_FIELD_TYPES,
    TYPE_CODE_PATTERN,
    FieldRole,
    FieldType,
)
from app.exceptions.tickets import TicketError
from app.utils.slug import slugify


def _fail(message: str, field: str | None = None) -> TicketError:
    return TicketError("validation", message, field=field)


def normalize_code(name: str, given: str | None) -> str:
    code = (given or "").strip() or slugify(name)
    if not TYPE_CODE_PATTERN.match(code):
        raise _fail(
            "El código del tipo debe empezar con una letra minúscula y usar solo letras "
            "minúsculas, números y guion bajo.",
            "code",
        )
    return code


def normalize_fields(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not raw:
        raise _fail("Agrega al menos un campo al formulario.", "fields")
    if len(raw) > MAX_FIELDS:
        raise _fail(f"Un tipo admite como máximo {MAX_FIELDS} campos.", "fields")
    fields: list[dict[str, Any]] = []
    keys: set[str] = set()
    roles: dict[str, str] = {}
    for index, item in enumerate(raw, start=1):
        label = str(item.get("label") or "").strip()
        if not label:
            raise _fail("Cada campo necesita una etiqueta.", "fields")
        key = str(item.get("key") or "").strip() or slugify(label) or f"campo_{index}"
        if not KEY_PATTERN.match(key):
            raise _fail(
                f"La clave «{key}» no es válida: usa minúsculas, números y guion bajo.", "fields"
            )
        if key in keys:
            raise _fail(f"La clave «{key}» está repetida en el formulario.", "fields")
        keys.add(key)
        kind = FieldType(item["type"])
        options = item.get("options")
        catalog = item.get("catalog") or None
        if kind == FieldType.SELECT:
            if bool(options) == bool(catalog):
                raise _fail(
                    f"El campo «{label}» debe tomar sus opciones de un catálogo o de una lista "
                    "propia, no de ambos ni de ninguno.",
                    "fields",
                )
        elif kind == FieldType.CHECKBOX_GROUP:
            if not options or catalog:
                raise _fail(f"El campo «{label}» necesita una lista de opciones.", "fields")
        elif options or catalog:
            raise _fail(f"El campo «{label}» no admite opciones ni catálogo.", "fields")
        if catalog and is_user_source(catalog):
            if not USER_SOURCE_PATTERN.match(catalog):
                raise _fail(f"La fuente «{catalog}» no es válida.", "fields")
        elif catalog and catalog not in CATALOG_KEYS:
            raise _fail(f"El catálogo «{catalog}» no existe.", "fields")
        if options:
            if len(options) > MAX_OPTIONS:
                raise _fail(f"El campo «{label}» tiene demasiadas opciones.", "fields")
            values = [str(o["value"]) for o in options]
            if len(set(values)) != len(values):
                raise _fail(f"El campo «{label}» tiene opciones repetidas.", "fields")
            options = [{"value": str(o["value"]), "label": str(o["label"])} for o in options]
        role = item.get("role") or None
        if role is not None:
            role = FieldRole(role)
            if kind not in ROLE_FIELD_TYPES[role]:
                raise _fail(
                    f"El campo «{label}» no puede tener el rol «{role.value}» con ese tipo.",
                    "fields",
                )
            if role.value in roles:
                raise _fail(
                    f"Solo un campo puede tener el rol «{role.value}»: "
                    f"«{roles[role.value]}» ya lo tiene.",
                    "fields",
                )
            roles[role.value] = label
        read_only = bool(item.get("read_only"))
        required = bool(item.get("required"))
        if read_only and required:
            raise _fail(f"El campo «{label}» no puede ser obligatorio y de solo lectura.", "fields")
        if kind == FieldType.FILE and (required or role):
            raise _fail(f"El campo de archivo «{label}» no puede ser obligatorio.", "fields")
        fields.append(
            {
                "key": key,
                "label": label,
                "type": kind.value,
                "required": required,
                "width": str(item.get("width") or "full"),
                "help": item.get("help") or None,
                "options": options or None,
                "catalog": catalog,
                "read_only": read_only,
                "role": role.value if role else None,
                "depends_on": str(item.get("depends_on") or "").strip() or None,
                "view_permission": item.get("view_permission") or None,
                "edit_permission": item.get("edit_permission") or None,
                "sort_order": index,
            }
        )
    kinds = {f["key"]: f["type"] for f in fields}
    for item in fields:
        parent = item["depends_on"]
        if parent is None:
            continue
        if item["type"] != FieldType.SELECT.value or parent == item["key"]:
            raise _fail(f"El campo «{item['label']}» no puede depender de «{parent}».", "fields")
        if kinds.get(parent) != FieldType.SELECT.value:
            raise _fail(
                f"El campo «{item['label']}» depende de «{parent}», que no es una lista del mismo "
                "formulario.",
                "fields",
            )
    if FieldRole.CURRENCY.value in roles and FieldRole.AMOUNT.value not in roles:
        raise _fail("Un campo «moneda» necesita un campo «monto».", "fields")
    if FieldRole.UNIT.value in roles and FieldRole.QUANTITY.value not in roles:
        raise _fail("Un campo «unidad» necesita un campo «cantidad».", "fields")
    return fields


def normalize_statuses(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not raw:
        raise _fail("Agrega al menos un estado.", "statuses")
    if len(raw) > MAX_STATUSES:
        raise _fail(f"Un tipo admite como máximo {MAX_STATUSES} estados.", "statuses")
    statuses: list[dict[str, Any]] = []
    codes: set[str] = set()
    for index, item in enumerate(raw, start=1):
        name = str(item.get("name") or "").strip()
        if not name:
            raise _fail("Cada estado necesita un nombre.", "statuses")
        code = str(item.get("code") or "").strip() or slugify(name)
        if not KEY_PATTERN.match(code):
            raise _fail(f"El código de estado «{code}» no es válido.", "statuses")
        if code in codes:
            raise _fail(f"El código de estado «{code}» está repetido.", "statuses")
        codes.add(code)
        statuses.append(
            {
                "code": code,
                "name": name,
                "color": str(item.get("color") or "default"),
                "sort_order": index,
                "is_initial": bool(item.get("is_initial")),
                "is_final": bool(item.get("is_final")),
                "counts_as_committed": item.get("counts_as_committed", True) is not False,
                "next_codes": item.get("next_codes"),
            }
        )
    initial = [s for s in statuses if s["is_initial"]]
    if len(initial) > 1:
        raise _fail("Solo un estado puede ser el inicial.", "statuses")
    if not initial:
        statuses[0]["is_initial"] = True
    start = next(s for s in statuses if s["is_initial"])
    if start["is_final"]:
        raise _fail("El estado inicial no puede ser final.", "statuses")
    if not any(s["is_final"] for s in statuses):
        raise _fail(
            "Define al menos un estado final: sin él ningún ticket podría cerrarse.", "statuses"
        )
    for status in statuses:
        targets = status["next_codes"]
        if targets is None:
            continue
        targets = list(dict.fromkeys(str(t) for t in targets))
        unknown = [t for t in targets if t not in codes]
        if unknown:
            raise _fail(f"Transición a un estado que no existe: {', '.join(unknown)}.", "statuses")
        if status["code"] in targets:
            raise _fail(f"El estado «{status['name']}» no puede pasar a sí mismo.", "statuses")
        if status["is_final"] and targets:
            raise _fail(
                f"El estado final «{status['name']}» no admite transiciones: es inmutable.",
                "statuses",
            )
        status["next_codes"] = targets
    return statuses


def normalize_steps(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if len(raw) > MAX_STEPS:
        raise _fail(f"Un tipo admite como máximo {MAX_STEPS} pasos.", "steps")
    steps: list[dict[str, Any]] = []
    codes: set[str] = set()
    for index, item in enumerate(raw, start=1):
        name = str(item.get("name") or "").strip()
        if not name:
            raise _fail("Cada paso necesita un nombre.", "steps")
        code = str(item.get("code") or "").strip() or slugify(name)
        if not KEY_PATTERN.match(code):
            raise _fail(f"El código de paso «{code}» no es válido.", "steps")
        if code in codes:
            raise _fail(f"El código de paso «{code}» está repetido.", "steps")
        codes.add(code)
        steps.append(
            {
                "code": code,
                "name": name,
                "role_id": item.get("role_id"),
                "responsible_area": item.get("responsible_area") or None,
                "required": item.get("required", True) is not False,
                "requires_signature": item.get("requires_signature", False) is True,
                "sort_order": index,
            }
        )
    return steps
