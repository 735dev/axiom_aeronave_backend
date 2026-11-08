"""Per-request audit context, carried in contextvars.

The access middleware binds the actor from the verified token. Repositories read it
when they append an audit event, so the actor can never come from a request body.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.config.audit import SYSTEM_ACTOR_ID, SYSTEM_ACTOR_NAME, SYSTEM_ACTOR_ROLE


@dataclass(frozen=True)
class Actor:
    id: str
    name: str
    role: str


@dataclass(frozen=True)
class Origin:
    ip: str | None = None
    user_agent: str | None = None


SYSTEM_ACTOR = Actor(SYSTEM_ACTOR_ID, SYSTEM_ACTOR_NAME, SYSTEM_ACTOR_ROLE)

_actor: ContextVar[Actor] = ContextVar("audit_actor", default=SYSTEM_ACTOR)
_origin: ContextVar[Origin] = ContextVar("audit_origin", default=Origin())
_suppressed: ContextVar[bool] = ContextVar("audit_suppressed", default=False)


def current_actor() -> Actor:
    return _actor.get()


def current_origin() -> Origin:
    return _origin.get()


def audit_is_suppressed() -> bool:
    return _suppressed.get()


@contextmanager
def bound_request(actor: Actor | None, origin: Origin) -> Iterator[None]:
    actor_token = _actor.set(actor or SYSTEM_ACTOR)
    origin_token = _origin.set(origin)
    try:
        yield
    finally:
        _actor.reset(actor_token)
        _origin.reset(origin_token)


@contextmanager
def without_audit() -> Iterator[None]:
    """Seed/maintenance scripts only: writes made inside leave no per-row audit event."""
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)
