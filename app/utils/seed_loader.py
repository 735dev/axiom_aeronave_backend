"""Reads the seed file shipped with the contract (app/fixtures/seeds.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEEDS_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "seeds.json"


@dataclass(frozen=True)
class SeedData:
    modules: list[dict[str, Any]]
    permissions: list[dict[str, Any]]
    roles: list[dict[str, Any]]
    users: list[dict[str, Any]]
    catalogs: dict[str, list[dict[str, Any]]]
    # Owned by the tickets module (loaded by its seed hook, not by the foundation).
    ticket_types: list[dict[str, Any]] = field(default_factory=list)
    business_rules: list[dict[str, Any]] = field(default_factory=list)
    # Per-type permissions (tickets.edit.<type>) granted to a role when the seed creates it:
    # they only exist once the ticket types are seeded, so roles cannot list them.
    role_type_grants: dict[str, list[str]] = field(default_factory=dict)


def load_seeds(path: Path = SEEDS_PATH) -> SeedData:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return SeedData(
        modules=raw["modules"],
        permissions=raw["permissions"],
        roles=raw["roles"],
        users=raw["users"],
        catalogs=raw["catalogs"],
        ticket_types=raw.get("ticket_types", []),
        business_rules=raw.get("business_rules", []),
        role_type_grants=raw.get("role_type_grants", {}),
    )
