"""Dual-currency money with a frozen rate.

Port of the reference `resolveMoney`. The base currency is USD. A rate is the amount of
base currency per unit of the ticket currency (the inverse of the local-currency-per-
dollar `exchange_rate` that operations and expenses carry).

Two rules are not negotiable:
1. The rate is frozen at the first resolution. Editing a note, or the operation changing
   its rate later, never revalues an existing amount. Only a change of amount, currency or
   of the rate written on the ticket itself revalues it.
2. An amount in a non-base currency without a usable rate is refused, never converted
   with an invented one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.config.tickets import (
    AMOUNT_MAX,
    BASE_CURRENCY,
    MONEY_PLACES,
    RATE_PLACES,
    RATE_TOLERANCE,
)

_MONEY_QUANTUM = Decimal(1).scaleb(-MONEY_PLACES)
_RATE_QUANTUM = Decimal(1).scaleb(-RATE_PLACES)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str
    currency_name: str
    rate: Decimal
    amount_base: Decimal
    base_currency: str
    rated_at: datetime
    rate_source: str  # base | operation | manual


@dataclass(frozen=True)
class MoneyResult:
    money: Money | None = None
    #: Why the amount could not be valued. Blocks the write.
    error: str | None = None


def to_decimal(value: Any) -> Decimal | None:
    """A finite number from a JSON number or numeric string; None when absent or invalid."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def valid_amount(value: Any) -> Decimal | None:
    """An amount the system accepts: finite, non-negative and below the ceiling."""
    number = to_decimal(value)
    if number is None or number < 0 or number > AMOUNT_MAX:
        return None
    return number


def _same_rate(previous: Money, manual: Decimal | None) -> bool:
    if previous.currency == BASE_CURRENCY:
        return True  # a rate means nothing for an amount already in the base currency
    if manual is None:
        return previous.rate_source != "manual"
    if manual <= 0:
        return False
    return abs(float(1 / manual) - float(previous.rate)) < RATE_TOLERANCE


def resolve_money(
    *,
    amount_value: Any,
    currency: str | None,
    currency_name: str | None,
    manual_rate: Any,
    parent_rate: Any,
    previous: Money | None = None,
    now: datetime | None = None,
) -> MoneyResult:
    """Value an amount. `manual_rate` and `parent_rate` are local currency per dollar."""
    if amount_value is None or (isinstance(amount_value, str) and not amount_value.strip()):
        return MoneyResult()
    amount = valid_amount(amount_value)
    if amount is None:
        return MoneyResult(error="El monto no es válido: debe ser un número no negativo.")
    if amount != amount.quantize(_MONEY_QUANTUM):
        return MoneyResult(error="El monto admite hasta dos decimales.")

    code = (currency or BASE_CURRENCY).strip().upper() or BASE_CURRENCY
    manual = to_decimal(manual_rate)
    if (
        previous is not None
        and previous.amount == amount
        and previous.currency == code
        and _same_rate(previous, manual)
    ):
        return MoneyResult(money=previous)

    moment = now or datetime.now(timezone.utc)
    name = currency_name or code
    if code == BASE_CURRENCY:
        return MoneyResult(
            money=Money(
                amount=amount,
                currency=code,
                currency_name=name,
                rate=Decimal(1),
                amount_base=amount.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP),
                base_currency=BASE_CURRENCY,
                rated_at=moment,
                rate_source="base",
            )
        )

    inherited = to_decimal(parent_rate)
    per_base = manual if manual is not None and manual > 0 else inherited
    if per_base is None or per_base <= 0:
        return MoneyResult(
            error=(
                f"Falta la tasa del día para convertir {code} a {BASE_CURRENCY}. "
                "Cárgala en la operación o en el gasto."
            )
        )
    return MoneyResult(
        money=Money(
            amount=amount,
            currency=code,
            currency_name=name,
            rate=(Decimal(1) / per_base).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP),
            amount_base=(amount / per_base).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP),
            base_currency=BASE_CURRENCY,
            rated_at=moment,
            rate_source="manual" if manual is not None and manual > 0 else "operation",
        )
    )
