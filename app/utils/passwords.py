"""Password generation and policy helpers."""

from __future__ import annotations

import secrets

from app.config.auth import PASSWORD_MIN_LENGTH, TEMPORARY_PASSWORD_BYTES
from app.config.security import password_too_long


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(TEMPORARY_PASSWORD_BYTES)


def generate_unusable_password() -> str:
    """Random secret nobody knows: a new account cannot log in until a reset is issued."""
    return secrets.token_urlsafe(32)


def password_problem(plain: str) -> str | None:
    """Spanish reason why `plain` is not acceptable as a new password, or None."""
    if len(plain) < PASSWORD_MIN_LENGTH:
        return f"La contraseña debe tener al menos {PASSWORD_MIN_LENGTH} caracteres."
    if password_too_long(plain):
        return "La contraseña es demasiado larga (máximo 72 bytes)."
    if plain.strip() != plain:
        return "La contraseña no puede empezar ni terminar con espacios."
    return None
