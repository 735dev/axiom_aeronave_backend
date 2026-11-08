"""The authenticated caller, resolved once per request by the access middleware."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request

from app.exceptions.auth import AuthError


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    full_name: str
    role_id: UUID
    role_code: str
    role_name: str
    permissions: frozenset[str]
    token_issued_at: float
    #: A temporary password is still in place: only the routes that fix it are open.
    must_change_password: bool = False


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        # Reaching a protected service without a principal means the registry is wrong.
        raise AuthError("invalid_token", "Tu sesión no es válida o expiró.")
    return principal
