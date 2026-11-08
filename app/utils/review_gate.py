"""Review gate: a low-confidence reading cannot be approved blind.

A field the machine was unsure about must be corrected by the person or explicitly marked
as reviewed. Approving it untouched and unmarked is exactly what the validation console
exists to prevent. Enforced here, in the server, not in the screen.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.config.tickets import Confidence


def unreviewed_fields(
    proposal: Iterable[Mapping[str, Any]],
    approved: Mapping[str, Any],
    confirmed: Iterable[str],
) -> list[Mapping[str, Any]]:
    """Low-confidence fields that were neither corrected nor confirmed.

    A field the caller did not send back counts as unreviewed (it is approved as read).
    """
    marked = set(confirmed)
    pending = []
    for item in proposal:
        if item.get("confidence") != Confidence.LOW.value or item["key"] in marked:
            continue
        sent = approved.get(item["key"], item["value"])
        if str(sent) == str(item["value"]):
            pending.append(item)
    return pending


def corrections(
    proposal: Iterable[Mapping[str, Any]], approved: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """What the person changed with respect to the reading."""
    changed = []
    for item in proposal:
        if item["key"] in approved and str(approved[item["key"]]) != str(item["value"]):
            changed.append({"key": item["key"], "from": item["value"], "to": approved[item["key"]]})
    return changed
