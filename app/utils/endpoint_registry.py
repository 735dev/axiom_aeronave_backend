"""Permission registry: every mounted route must be declared in endpoint_permissions.json.

An unregistered route would be open, so the application refuses to start when the
mounted routes and the registry disagree (see `assert_routes_registered`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "endpoint_permissions.json"


class Access(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    PERMISSION = "permission"


@dataclass(frozen=True)
class RouteRule:
    access: Access
    permissions: tuple[str, ...] = ()
    # A held code that extends a required one also qualifies (tickets.view.<type>).
    prefix_match: bool = False
    description: str = ""


def route_key(method: str, template: str) -> str:
    return f"{method.upper()} {template}"


def _parse_rule(key: str, raw: dict) -> RouteRule:
    access = Access(raw["access"])
    permissions = tuple(raw.get("permissions", ()))
    if access is Access.PERMISSION and not permissions:
        raise ValueError(f"{key}: access 'permission' needs a non-empty 'permissions' list")
    if access is not Access.PERMISSION and permissions:
        raise ValueError(f"{key}: only 'permission' access may list permissions")
    return RouteRule(
        access=access,
        permissions=permissions,
        prefix_match=bool(raw.get("prefix_match", False)),
        description=str(raw.get("description", "")),
    )


@lru_cache
def load_registry(path: Path = REGISTRY_PATH) -> dict[str, RouteRule]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {key: _parse_rule(key, value) for key, value in raw.items()}


def mounted_routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def mounted_keys(app: FastAPI) -> set[str]:
    return {
        route_key(method, route.path)
        for route in mounted_routes(app)
        for method in route.methods - {"HEAD", "OPTIONS"}
    }


def assert_routes_registered(app: FastAPI) -> None:
    registry = load_registry()
    mounted = mounted_keys(app)
    missing = sorted(mounted - set(registry))
    stale = sorted(set(registry) - mounted)
    if missing or stale:
        raise RuntimeError(
            "Route registry out of sync. Unregistered (would be open): "
            f"{missing}. Registered but not mounted: {stale}."
        )


class RouteMatcher:
    """Resolves (method, path) to its registry rule using the mounted route patterns."""

    def __init__(self, app: FastAPI):
        registry = load_registry()
        self._entries: list[tuple[re.Pattern[str], frozenset[str], RouteRule]] = []
        for route in mounted_routes(app):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                rule = registry.get(route_key(method, route.path))
                if rule is not None:
                    self._entries.append((route.path_regex, frozenset({method}), rule))

    def match(self, method: str, path: str) -> RouteRule | None:
        for regex, methods, rule in self._entries:
            if method.upper() in methods and regex.match(path):
                return rule
        return None
