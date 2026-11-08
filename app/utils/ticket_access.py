"""Who may see, create and edit which ticket type.

A global permission (`tickets.view`) covers every type; a derived one
(`tickets.view.expense`) covers only its own. Pure functions over the caller's effective
permission codes.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.config.tickets import PERM_CREATE, PERM_EDIT, PERM_MANAGE_TYPES, PERM_VIEW


def _derived(held: Iterable[str], base: str) -> set[str]:
    prefix = f"{base}."
    return {code[len(prefix) :] for code in held if code.startswith(prefix)}


def allowed(held: frozenset[str], base: str, type_code: str) -> bool:
    return base in held or f"{base}.{type_code}" in held


def can_view(held: frozenset[str], type_code: str) -> bool:
    return allowed(held, PERM_VIEW, type_code)


def can_create(held: frozenset[str], type_code: str) -> bool:
    return allowed(held, PERM_CREATE, type_code)


def can_edit(held: frozenset[str], type_code: str) -> bool:
    return allowed(held, PERM_EDIT, type_code)


def viewable_type_codes(held: frozenset[str]) -> set[str] | None:
    """Type codes whose tickets the caller may read; None means every type."""
    if PERM_VIEW in held:
        return None
    return _derived(held, PERM_VIEW)


def visible_type_codes_for_definitions(held: frozenset[str]) -> set[str] | None:
    """Type codes whose definition (form) the caller may read.

    Anyone who can view, create or edit a type needs its form; managing types opens all.
    """
    if PERM_MANAGE_TYPES in held or {PERM_VIEW, PERM_CREATE, PERM_EDIT} & held:
        return None
    return _derived(held, PERM_VIEW) | _derived(held, PERM_CREATE) | _derived(held, PERM_EDIT)
