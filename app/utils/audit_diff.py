"""Field-level diff for the audit trail."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def jsonable(value: Any) -> Any:
    """Plain JSON value for a column value, so it can be stored in the audit row."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(jsonable(v) for v in value)
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return value


def is_empty(value: Any) -> bool:
    return value is None or value == ""


def diff_values(
    before: dict[str, Any], after: dict[str, Any], labels: dict[str, str]
) -> list[dict[str, Any]]:
    """Fields that changed, with their Spanish label.

    A field that goes from absent to an empty string is not a change and must not
    pollute the trail.
    """
    changes: list[dict[str, Any]] = []
    for field in dict.fromkeys([*before.keys(), *after.keys()]):
        old, new = jsonable(before.get(field)), jsonable(after.get(field))
        if is_empty(old) and is_empty(new):
            continue
        if old == new:
            continue
        changes.append({"field": field, "label": labels.get(field, field), "from": old, "to": new})
    return changes
