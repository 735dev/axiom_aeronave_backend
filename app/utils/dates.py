"""Date-range parsing for filters. Day boundaries follow the business time zone."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import get_settings


def business_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().BUSINESS_TIMEZONE)


def parse_range_bound(raw: str) -> tuple[datetime, bool]:
    """Parse `YYYY-MM-DD` or an ISO datetime. Returns (instant, is_date_only).

    A date-only value is the start of that day in the business zone; a naive datetime is
    read in the business zone too. Raises ValueError on anything else.
    """
    text = raw.strip()
    zone = business_zone()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return datetime.combine(day, time.min, tzinfo=zone), True
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid date: {raw!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return moment, False


def end_of_day_exclusive(start_of_day: datetime) -> datetime:
    """Start of the next day: `to=2026-08-21` must include every event of the 21st."""
    return start_of_day + timedelta(days=1)
