"""Effective permissions: role permissions, plus grants, minus revocations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.config.access import OverrideEffect, PermissionOrigin
from app.models.access import AppUser, Permission


@dataclass(frozen=True)
class EffectivePermission:
    code: str
    name: str
    origin: PermissionOrigin


def _active(permissions: Iterable[Permission]) -> list[Permission]:
    return [p for p in permissions if p.is_active]


def role_codes(user: AppUser) -> set[str]:
    return {p.code for p in _active(user.role.permissions)}


def override_codes(user: AppUser, effect: OverrideEffect) -> set[str]:
    return {
        o.permission.code
        for o in user.permission_overrides
        if o.effect == effect.value and o.permission.is_active
    }


def effective_codes(user: AppUser) -> frozenset[str]:
    """What the user can actually do. A revocation always wins over the role."""
    granted = override_codes(user, OverrideEffect.GRANT)
    revoked = override_codes(user, OverrideEffect.REVOKE)
    return frozenset((role_codes(user) | granted) - revoked)


def effective_breakdown(user: AppUser) -> list[EffectivePermission]:
    """Every permission that touches the user, with where it comes from."""
    from_role = role_codes(user)
    granted = override_codes(user, OverrideEffect.GRANT)
    revoked = override_codes(user, OverrideEffect.REVOKE)
    by_code: dict[str, Permission] = {p.code: p for p in _active(user.role.permissions)}
    for override in user.permission_overrides:
        by_code.setdefault(override.permission.code, override.permission)

    rows: list[EffectivePermission] = []
    for code in sorted(by_code):
        if code in revoked:
            origin = PermissionOrigin.REVOKED
        elif code in from_role:
            origin = PermissionOrigin.ROLE
        elif code in granted:
            origin = PermissionOrigin.GRANTED
        else:
            continue
        rows.append(EffectivePermission(code, by_code[code].name, origin))
    return rows


def has_permission(
    held: Iterable[str], required: Iterable[str], prefix_match: bool = False
) -> bool:
    """True when any required code is held. With `prefix_match`, a held code that extends
    a required one (`tickets.view.expense` for `tickets.view`) also qualifies."""
    held_set = set(held)
    for code in required:
        if code in held_set:
            return True
        if prefix_match and any(h.startswith(f"{code}.") for h in held_set):
            return True
    return False
