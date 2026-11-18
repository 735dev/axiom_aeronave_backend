"""Whether an operation is domestic or international.

A point is an airport already chosen on the form, or the first token of a stop line.
The airport catalog stores `domestic` or `international` in `extra`. One international
point makes the whole operation international. Unknown codes are not treated as foreign.
"""

from __future__ import annotations

from typing import Any

POINT_KEYS = ("origin", "destination", "alternate")


def route_points(payload: dict[str, Any]) -> list[str]:
    points: list[str] = []
    for key in POINT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            points.append(value.strip())
    stops = payload.get("stops")
    if isinstance(stops, str):
        for line in stops.splitlines():
            token = line.strip().split()[0] if line.strip() else ""
            if token:
                points.append(token)
    return points


def scope_for(points: list[str], extras: dict[str, str | None]) -> str:
    for code in points:
        extra = (extras.get(code) or "").strip().lower()
        if extra == "international":
            return "international"
    return "domestic"
