"""Login throttling with a pluggable counter store.

The default store lives in process memory: with N replicas the real limit is
`limit x N`. Set REDIS_URL to share the counters across replicas. If Redis fails the
limiter degrades to the local memory store (and logs) instead of opening the door.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Protocol

from app.config.settings import Settings, get_settings
from app.exceptions.rate_limit import RateLimitError

logger = logging.getLogger(__name__)

KEY_PREFIX = "aero:rl:"


class RateLimitStore(Protocol):
    async def hit(self, key: str, window_seconds: int) -> int:
        """Increment the counter (the TTL starts at the first hit). Returns the new count."""

    async def count(self, key: str) -> int: ...

    async def ttl(self, key: str) -> int:
        """Seconds until the key expires (0 if absent)."""

    async def reset(self, key: str) -> None: ...

    async def close(self) -> None: ...


class MemoryRateLimitStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[int, float]] = {}

    def _live(self, key: str) -> tuple[int, float] | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry[1] <= time.monotonic():
            del self._data[key]
            return None
        return entry

    async def hit(self, key: str, window_seconds: int) -> int:
        entry = self._live(key)
        if entry is None:
            self._data[key] = (1, time.monotonic() + window_seconds)
            return 1
        self._data[key] = (entry[0] + 1, entry[1])
        return entry[0] + 1

    async def count(self, key: str) -> int:
        entry = self._live(key)
        return entry[0] if entry else 0

    async def ttl(self, key: str) -> int:
        entry = self._live(key)
        return max(1, int(entry[1] - time.monotonic())) if entry else 0

    async def reset(self, key: str) -> None:
        self._data.pop(key, None)

    async def close(self) -> None:
        self._data.clear()


class RedisRateLimitStore:
    def __init__(self, url: str, timeout: float) -> None:
        from redis import asyncio as aioredis

        self._client = aioredis.from_url(
            url, socket_timeout=timeout, socket_connect_timeout=timeout, decode_responses=True
        )

    async def hit(self, key: str, window_seconds: int) -> int:
        pipe = self._client.pipeline()
        pipe.incr(KEY_PREFIX + key)
        pipe.expire(KEY_PREFIX + key, window_seconds, nx=True)
        count, _ = await pipe.execute()
        return int(count)

    async def count(self, key: str) -> int:
        value = await self._client.get(KEY_PREFIX + key)
        return int(value) if value else 0

    async def ttl(self, key: str) -> int:
        ttl = await self._client.ttl(KEY_PREFIX + key)
        return max(0, int(ttl))

    async def reset(self, key: str) -> None:
        await self._client.delete(KEY_PREFIX + key)

    async def close(self) -> None:
        await self._client.aclose()


class ResilientStore:
    """Primary store with a local fallback when the primary raises."""

    def __init__(self, primary: RateLimitStore, fallback: RateLimitStore) -> None:
        self._primary = primary
        self._fallback = fallback

    async def _call(self, name: str, *args):
        try:
            return await getattr(self._primary, name)(*args)
        except Exception:  # noqa: BLE001 - any Redis failure must degrade, not open the door
            logger.exception("rate limit store failed; using local memory for %s", name)
            return await getattr(self._fallback, name)(*args)

    async def hit(self, key: str, window_seconds: int) -> int:
        return await self._call("hit", key, window_seconds)

    async def count(self, key: str) -> int:
        return await self._call("count", key)

    async def ttl(self, key: str) -> int:
        return await self._call("ttl", key)

    async def reset(self, key: str) -> None:
        await self._call("reset", key)

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()


_store: RateLimitStore | None = None


def get_rate_limit_store() -> RateLimitStore:
    global _store
    if _store is None:
        settings = get_settings()
        if settings.REDIS_URL:
            _store = ResilientStore(
                RedisRateLimitStore(settings.REDIS_URL, settings.REDIS_TIMEOUT_SECONDS),
                MemoryRateLimitStore(),
            )
        else:
            _store = MemoryRateLimitStore()
    return _store


async def close_rate_limit_store() -> None:
    global _store
    if _store is not None:
        await _store.close()
        _store = None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


class LoginThrottle:
    """Failed-login budget per account (email) and per client IP.

    The account budget is keyed by the attempted email whether or not it exists, so a
    lock-out looks identical for real and invented accounts.
    """

    def __init__(self, store: RateLimitStore, settings: Settings) -> None:
        self._store = store
        self._max_email = settings.LOGIN_MAX_FAILS_PER_EMAIL
        self._max_ip = settings.LOGIN_MAX_FAILS_PER_IP
        self._window = settings.LOGIN_LOCK_SECONDS

    @staticmethod
    def _email_key(email: str) -> str:
        return f"login:email:{_digest(email.strip().lower())}"

    @staticmethod
    def _ip_key(ip: str) -> str:
        return f"login:ip:{_digest(ip)}"

    async def retry_after(self, email: str, ip: str) -> int:
        """Seconds the caller must wait, or 0 when it may try."""
        waits = []
        if await self._store.count(self._email_key(email)) >= self._max_email:
            waits.append(await self._store.ttl(self._email_key(email)))
        if await self._store.count(self._ip_key(ip)) >= self._max_ip:
            waits.append(await self._store.ttl(self._ip_key(ip)))
        return max(waits) if waits else 0

    async def register_failure(self, email: str, ip: str) -> None:
        await self._store.hit(self._email_key(email), self._window)
        await self._store.hit(self._ip_key(ip), self._window)

    async def register_success(self, email: str) -> None:
        await self._store.reset(self._email_key(email))

    async def first_block_notice(self, email: str, ip: str) -> bool:
        """True once per lock window and per locked subject (account or IP).

        Used to audit a lock-out without logging every probe, and without one row per
        source IP when the same account is attacked from many addresses.
        """
        if await self._store.count(self._email_key(email)) >= self._max_email:
            key = f"login:blocked:email:{_digest(email.strip().lower())}"
        else:
            key = f"login:blocked:ip:{_digest(ip)}"
        return await self._store.hit(key, self._window) == 1


class ActionThrottle:
    """Fixed-window budget of a costly action, per account AND per client IP.

    Every call counts, valid or not: the point is to cap what a caller can make the server
    do (a receipt read costs money, a search scans personal data), not to punish errors.
    Either budget being spent answers 429 with the seconds left in its window.
    """

    def __init__(self, store: RateLimitStore, settings: Settings) -> None:
        self._store = store
        self._enabled = settings.RATE_LIMIT_ENABLED
        self._window = settings.RATE_LIMIT_WINDOW_SECONDS
        self._limits = {
            "extract": (
                settings.RATE_LIMIT_EXTRACT_PER_ACCOUNT,
                settings.RATE_LIMIT_EXTRACT_PER_IP,
            ),
            "upload": (
                settings.RATE_LIMIT_UPLOAD_PER_ACCOUNT,
                settings.RATE_LIMIT_UPLOAD_PER_IP,
            ),
            "search": (
                settings.RATE_LIMIT_SEARCH_PER_ACCOUNT,
                settings.RATE_LIMIT_SEARCH_PER_IP,
            ),
            "export": (
                settings.RATE_LIMIT_EXPORT_PER_ACCOUNT,
                settings.RATE_LIMIT_EXPORT_PER_IP,
            ),
            "import": (
                settings.RATE_LIMIT_IMPORT_PER_ACCOUNT,
                settings.RATE_LIMIT_IMPORT_PER_IP,
            ),
            "audit_search": (
                settings.RATE_LIMIT_AUDIT_SEARCH_PER_ACCOUNT,
                settings.RATE_LIMIT_AUDIT_SEARCH_PER_IP,
            ),
            "heavy_list": (
                settings.RATE_LIMIT_HEAVY_LIST_PER_ACCOUNT,
                settings.RATE_LIMIT_HEAVY_LIST_PER_IP,
            ),
        }

    async def check(self, action: str, account_id: str, ip: str) -> None:
        if not self._enabled:
            return
        max_account, max_ip = self._limits[action]
        waits: list[int] = []
        for key, limit in (
            (f"act:{action}:acct:{account_id}", max_account),
            (f"act:{action}:ip:{_digest(ip)}", max_ip),
        ):
            if await self._store.hit(key, self._window) > limit:
                waits.append(await self._store.ttl(key) or self._window)
        if waits:
            raise RateLimitError(max(waits))


def get_action_throttle() -> ActionThrottle:
    return ActionThrottle(get_rate_limit_store(), get_settings())


def get_login_throttle() -> LoginThrottle:
    return LoginThrottle(get_rate_limit_store(), get_settings())
