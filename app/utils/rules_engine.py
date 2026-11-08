"""Business rules: declared conditions over a ticket, evaluated the same way every time.

Pure functions. A rule that fires is recorded on the ticket with the message it had at that
moment (editing the rule later never rewrites what was warned), and a firing keeps its
original timestamp when the rules are evaluated again.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.config.tickets import RuleAction, RuleField, RuleOperator
from app.utils.money import to_decimal


@dataclass(frozen=True)
class RuleLike:
    id: str
    name: str
    field: str
    operator: str
    value: str | None
    action: str
    message: str
    route_limits: Mapping[str, Any] | None


@dataclass(frozen=True)
class Facts:
    """What the engine knows about a ticket."""

    amount_base: Decimal | None
    attachments: int
    concept: str | None
    route: str | None


def route_of(origin: Any, destination: Any) -> str | None:
    if not origin or not destination:
        return None
    return f"{str(origin).strip().upper()}-{str(destination).strip().upper()}"


def _compare(operator: str, actual: Decimal | str | None, expected: str | None) -> bool:
    if operator == RuleOperator.MISSING:
        return not actual
    if actual is None or actual == "":
        return False
    if operator == RuleOperator.EQ:
        if isinstance(actual, Decimal):
            wanted = to_decimal(expected)
            return wanted is not None and actual == wanted
        return str(actual) == str(expected)
    # Numeric comparisons need numbers on both sides: comparing strings would give an
    # alphabetical order that looks right and is not.
    left, right = to_decimal(actual), to_decimal(expected)
    if left is None or right is None:
        return False
    return left > right if operator == RuleOperator.GT else left < right


def fires(rule: RuleLike, facts: Facts) -> bool:
    if rule.field == RuleField.AMOUNT_VS_ROUTE:
        # No limit for this route means nothing to compare with: do not invent one.
        limits = rule.route_limits or {}
        limit = to_decimal(limits.get(facts.route)) if facts.route else None
        return limit is not None and facts.amount_base is not None and facts.amount_base > limit
    if rule.field == RuleField.AMOUNT_BASE:
        actual: Decimal | str | None = facts.amount_base
    elif rule.field == RuleField.ATTACHMENTS:
        actual = str(facts.attachments) if facts.attachments else None
    elif rule.field == RuleField.CONCEPT:
        actual = facts.concept
    else:
        return False
    return _compare(rule.operator, actual, rule.value)


def evaluate(rules: Iterable[RuleLike], facts: Facts) -> list[RuleLike]:
    return [rule for rule in rules if fires(rule, facts)]


def merge_hits(
    previous: Sequence[Mapping[str, Any]], firing: Sequence[RuleLike], now: datetime | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(all current hits, the ones that fired for the first time).

    A rule that already fired keeps its record (timestamp and message); one that no longer
    matches is dropped.
    """
    moment = (now or datetime.now(timezone.utc)).isoformat()
    kept = {h["rule_id"]: dict(h) for h in previous}
    hits: list[dict[str, Any]] = []
    new: list[dict[str, Any]] = []
    for rule in firing:
        hit = kept.get(rule.id)
        if hit is None:
            hit = {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "action": rule.action,
                "message": rule.message,
                "at": moment,
            }
            new.append(hit)
        hits.append(hit)
    return hits, new


def requires_authorization(hits: Sequence[Mapping[str, Any]]) -> bool:
    return any(h.get("action") == RuleAction.REQUIRE_AUTHORIZATION for h in hits)


def missing_authorization(
    hits: Sequence[Mapping[str, Any]],
    authorization: Mapping[str, Any] | None,
    amount_base: Decimal | None,
) -> bool:
    """A flagged expense needs a current approval, tied to the amount it approved."""
    if not requires_authorization(hits):
        return False
    if not authorization:
        return True
    approved = to_decimal(authorization.get("amount_base"))
    return approved != (amount_base if amount_base is not None else Decimal(0))
