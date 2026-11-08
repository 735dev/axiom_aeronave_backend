"""Cross-field rules of the quick log (actual departure and arrival of a flight)."""

from __future__ import annotations

from typing import Any

from app.config.closing import (
    ARRIVAL_BEFORE_DEPARTURE_MESSAGE,
    ARRIVAL_KEY,
    DEPARTURE_KEY,
    NEXT_DAY_KEY,
    SAME_TIME_MESSAGE,
)


def _minutes(value: Any) -> int | None:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return None
    hours, minutes = value[:2], value[3:]
    if not (hours.isdigit() and minutes.isdigit()):
        return None
    return int(hours) * 60 + int(minutes)


def flight_time_problem(payload: dict[str, Any]) -> str | None:
    """A Spanish message when the times cannot belong to one flight, else None.

    Equal times are refused; an arrival earlier than the departure is only valid when the
    flight crossed midnight (`arrival_next_day`)."""
    left, back = _minutes(payload.get(DEPARTURE_KEY)), _minutes(payload.get(ARRIVAL_KEY))
    if left is None or back is None:
        return None
    if left == back:
        return SAME_TIME_MESSAGE
    if back < left and not payload.get(NEXT_DAY_KEY):
        return ARRIVAL_BEFORE_DEPARTURE_MESSAGE
    return None
