"""Reads the official Bs/USD rate from a public source and keeps it for a short time.

The destination is a setting, never something a user chooses (no SSRF surface), the call has an
explicit timeout and does not follow redirects. Anything unexpected in the answer (not a
positive number, wrong source, absurd value) is treated as "unavailable": the form then asks for
the rate by hand, it is never filled with a guessed value. Nothing is stored here: the rate is
frozen on the ticket when the person saves it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config.settings import Settings

logger = logging.getLogger(__name__)

#: Plausible bounds for bolivars per dollar. Outside them the answer is discarded.
MIN_RATE = 1.0
MAX_RATE = 1_000_000.0
FAKE_RATE = 100.0
#: Bolivars per euro in fake mode. Real mode never substitutes this number.
FAKE_EUR_RATE = 110.0
SOURCE = "oficial"
SUPPORTED = frozenset({"USD", "EUR"})


class ExchangeRateUnavailable(Exception):
    """The rate could not be read or did not pass validation."""


@dataclass(frozen=True)
class ExchangeRate:
    rate: float
    source: str
    effective_date: str | None
    fetched_at: datetime


_cache: dict[str, ExchangeRate] = {}
_cache_expires: dict[str, float] = {}


def clear_cache() -> None:
    _cache.clear()
    _cache_expires.clear()


def _parse(payload: Any, now: datetime) -> ExchangeRate:
    if not isinstance(payload, dict) or payload.get("fuente") != SOURCE:
        raise ExchangeRateUnavailable("unexpected source")
    value = payload.get("promedio")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExchangeRateUnavailable("rate is not a number")
    if not MIN_RATE <= float(value) <= MAX_RATE:
        raise ExchangeRateUnavailable("rate out of range")
    effective = payload.get("fechaActualizacion")
    effective_date = None
    if isinstance(effective, str):
        try:
            effective_date = (
                datetime.fromisoformat(effective.replace("Z", "+00:00")).date().isoformat()
            )
        except ValueError:
            effective_date = None
    return ExchangeRate(round(float(value), 4), SOURCE, effective_date, now)


async def get_exchange_rate(settings: Settings, currency: str = "USD") -> ExchangeRate:
    """Official bolivars per one unit of `currency` (`USD` or `EUR`).

    Callers that pass only the settings keep reading the dollar quote.
    """
    code = (currency or "USD").strip().upper()
    if code not in SUPPORTED:
        raise ExchangeRateUnavailable("unsupported currency")
    mode = settings.EXCHANGE_RATE_MODE.lower()
    now = datetime.now(timezone.utc)
    if mode == "fake":
        rate = FAKE_RATE if code == "USD" else FAKE_EUR_RATE
        return ExchangeRate(rate, SOURCE, now.date().isoformat(), now)
    if mode != "real":
        raise ExchangeRateUnavailable("exchange rate is switched off")

    cached = _cache.get(code)
    if cached is not None and time.monotonic() < _cache_expires.get(code, 0.0):
        return cached
    url = settings.EXCHANGE_RATE_URL if code == "USD" else settings.EXCHANGE_RATE_EUR_URL
    try:
        async with httpx.AsyncClient(
            timeout=settings.EXCHANGE_RATE_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            found = _parse(response.json(), now)
    except ExchangeRateUnavailable:
        logger.warning("exchange_rate_rejected currency=%s", code)
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("exchange_rate_unavailable currency=%s error=%s", code, type(exc).__name__)
        raise ExchangeRateUnavailable("source did not answer") from exc
    _cache[code] = found
    _cache_expires[code] = time.monotonic() + settings.EXCHANGE_RATE_CACHE_SECONDS
    return found
