"""Ticket codes.

Operations (root types) are numbered after the aircraft: registration, running number and
two-digit year (`YV2323-001-26`). The number is per aircraft and per year, so in January
every aircraft starts again at 001. Every other ticket uses a global `TK-0001`.

The numbers come from the `ticket_counter` table (see the repository), which never hands
the same number twice even after a ticket is cancelled and under concurrent creation.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config.settings import get_settings
from app.config.tickets import (
    CONTROL_NUMBER_PAD,
    CONTROL_NUMBER_PREFIX,
    COUNTER_CONTROL_SERIES,
    COUNTER_TICKET_SERIES,
    OPERATION_SEQUENCE_PAD,
    REGISTRATION_PATTERN,
    TICKET_CODE_PAD,
    TICKET_CODE_PREFIX,
)


def business_today() -> date:
    return datetime.now(ZoneInfo(get_settings().BUSINESS_TIMEZONE)).date()


def normalize_registration(value: object) -> str | None:
    """Aircraft registration in canonical form, or None when it is not a valid one."""
    text = str(value or "").strip().upper()
    return text if REGISTRATION_PATTERN.match(text) else None


def year_suffix(day: date) -> str:
    return f"{day.year % 100:02d}"


def ticket_series() -> str:
    return COUNTER_TICKET_SERIES


def operation_series(registration: str, yy: str) -> str:
    return f"op:{registration}:{yy}"


def control_series(yy: str) -> str:
    return f"{COUNTER_CONTROL_SERIES}:{yy}"


def ticket_code(number: int) -> str:
    return f"{TICKET_CODE_PREFIX}-{number:0{TICKET_CODE_PAD}d}"


def operation_code(registration: str, number: int, yy: str) -> str:
    return f"{registration}-{number:0{OPERATION_SEQUENCE_PAD}d}-{yy}"


def control_number(number: int, yy: str) -> str:
    return f"{CONTROL_NUMBER_PREFIX}{number:0{CONTROL_NUMBER_PAD}d}-{yy}"
