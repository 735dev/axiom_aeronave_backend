"""Status a ticket reaches from the rules stored on its type.

A status lists the conditions that move a ticket into it. A type with no
conditions keeps the manual status chips and the first-decision advance.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


def status_for_rules(
    statuses: Sequence[Mapping[str, Any]],
    payload: dict[str, Any] | None,
    decided_steps: Iterable[str],
    required_steps: Iterable[str],
    current_code: str,
) -> str | None:
    """The status to move to, or None when no stored rule applies.

    Completing every required non-signature step wins. Otherwise the matching
    condition with the latest status wins. A condition that no longer holds
    sends the ticket back to the first status that is neither initial nor final.
    """
    ruled = [status for status in statuses if status.get("advance_rules")]
    if not ruled:
        return None
    decided = set(decided_steps)
    required = list(required_steps)
    data = payload or {}
    if required and all(code in decided for code in required):
        completed = [
            status
            for status in ruled
            if any(rule.get("when") == "all_required" for rule in status["advance_rules"])
        ]
        if completed:
            return min(completed, key=lambda status: status.get("sort_order", 0))["code"]
    matches = [status for status in ruled if _matches(status, data, decided)]
    if matches:
        return max(matches, key=lambda status: status.get("sort_order", 0))["code"]
    current = next((status for status in ruled if status["code"] == current_code), None)
    if current is not None and not _matches(current, data, decided):
        progress = [
            status
            for status in statuses
            if not status.get("is_initial") and not status.get("is_final")
        ]
        if progress:
            return min(progress, key=lambda status: status.get("sort_order", 0))["code"]
    return None


def _matches(status: Mapping[str, Any], payload: Mapping[str, Any], decided: set[str]) -> bool:
    for rule in status.get("advance_rules") or []:
        if rule.get("when") == "all_required":
            continue
        step = rule.get("step_code")
        if not step or step not in decided:
            continue
        if rule.get("only_this_step") and decided != {step}:
            continue
        field = rule.get("field_key")
        if field and not _equals(payload.get(field), rule.get("equals")):
            continue
        return True
    return False


def _equals(value: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return value is expected
    if expected is None:
        return False
    return str(value) == str(expected)
