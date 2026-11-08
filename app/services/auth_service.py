"""Authentication: login, refresh, logout, own profile and password."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache

from fastapi import Request
from fastapi_basekit.aio.sqlalchemy.service.base import BaseService

from app.config.audit import (
    ANONYMOUS_ACTOR_FALLBACK_NAME,
    ANONYMOUS_ACTOR_ID,
    ANONYMOUS_ACTOR_ROLE,
    EMAIL_MAX_LENGTH,
    AuditAction,
    AuditEntity,
)
from app.config.auth import DECOY_PASSWORD, TokenType
from app.config.security import hash_password, verify_password
from app.exceptions.auth import AuthError
from app.models.access import AppUser
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.user import UserRepository
from app.utils.audit_context import Actor
from app.utils.client_ip import client_ip
from app.utils.effective_permissions import effective_codes
from app.utils.passwords import password_problem
from app.utils.principal import current_principal
from app.utils.rate_limit import LoginThrottle, get_login_throttle
from app.utils.tokens import TokenService

logger = logging.getLogger(__name__)

INVALID_CREDENTIALS = "Credenciales inválidas."
RATE_LIMITED = "Demasiados intentos fallidos. Espera unos minutos antes de volver a intentar."
INVALID_SESSION = "Tu sesión no es válida o expiró. Inicia sesión de nuevo."


@cache
def _decoy_hash() -> str:
    return hash_password(DECOY_PASSWORD)


@dataclass(frozen=True)
class AuthResult:
    user: AppUser
    permissions: list[str]
    token: str | None = None
    refresh_token: str | None = None


def _clean_email(raw: str) -> str:
    """Attempted email as shown in the trail: lower-cased, no control characters, capped."""
    cleaned = "".join(ch for ch in raw.strip().lower() if ch.isprintable())
    return cleaned[:EMAIL_MAX_LENGTH]


class AuthService(BaseService[AppUser]):
    repository: UserRepository
    events: AuditEventRepository

    def __init__(
        self,
        repository: UserRepository,
        request: Request | None = None,
        throttle: LoginThrottle | None = None,
        **kwargs,
    ):
        super().__init__(repository, request=request, **kwargs)
        self.throttle = throttle or get_login_throttle()
        self.tokens = TokenService()

    # ---- helpers --------------------------------------------------------------------

    def _ip(self) -> str:
        return client_ip(self.request)

    def _result(self, user: AppUser, with_tokens: bool) -> AuthResult:
        permissions = sorted(effective_codes(user))
        if not with_tokens:
            return AuthResult(user, permissions)
        return AuthResult(
            user,
            permissions,
            token=self.tokens.issue(user.id, TokenType.ACCESS),
            refresh_token=self.tokens.issue(user.id, TokenType.REFRESH),
        )

    @staticmethod
    def _usable(user: AppUser | None) -> bool:
        return user is not None and user.is_active and user.role.is_active

    async def _audit_failed_login(self, email: str, summary: str) -> None:
        # Own transaction: the request is about to fail and roll back.
        await self.events.append_independent(
            AuditEntry(
                action=AuditAction.LOGIN_FAILED,
                entity=AuditEntity.SESSION,
                entity_id=email or ANONYMOUS_ACTOR_FALLBACK_NAME,
                entity_label=email or ANONYMOUS_ACTOR_FALLBACK_NAME,
                summary=summary,
                actor=Actor(
                    ANONYMOUS_ACTOR_ID, email or ANONYMOUS_ACTOR_FALLBACK_NAME, ANONYMOUS_ACTOR_ROLE
                ),
            )
        )

    # ---- commands -------------------------------------------------------------------

    async def login(self, email: str, password: str) -> AuthResult:
        attempted = _clean_email(email)
        ip = self._ip()

        wait = await self.throttle.retry_after(attempted, ip)
        if wait:
            if await self.throttle.first_block_notice(attempted, ip):
                await self._audit_failed_login(
                    attempted,
                    f"Acceso bloqueado por exceso de intentos con {attempted or 'correo vacío'}",
                )
            raise AuthError("rate_limited", RATE_LIMITED, retry_after=wait)

        user = await self.repository.get_by_email(attempted)
        # Unknown email: verify against a decoy so the timing matches a wrong password.
        password_ok = verify_password(password, user.password_hash if user else _decoy_hash())
        if not (self._usable(user) and password_ok):
            await self.throttle.register_failure(attempted, ip)
            await self._audit_failed_login(
                attempted, f"Intento de acceso fallido con {attempted or 'correo vacío'}"
            )
            raise AuthError("invalid_credentials", INVALID_CREDENTIALS)

        await self.throttle.register_success(attempted)
        await self.repository.record_login(user)
        await self.events.append(
            AuditEntry(
                action=AuditAction.LOGIN,
                entity=AuditEntity.SESSION,
                entity_id=str(user.id),
                entity_label=user.email,
                summary=f"{user.full_name} inició sesión",
                actor=Actor(str(user.id), user.full_name, user.role.name),
            )
        )
        return self._result(user, with_tokens=True)

    async def refresh(self, refresh_token: str) -> AuthResult:
        claims = self.tokens.read(refresh_token, TokenType.REFRESH)
        user = await self.repository.get_for_session(claims.user_id) if claims else None
        if claims is None or not self._usable(user):
            raise AuthError("invalid_token", INVALID_SESSION)
        valid_after = user.tokens_valid_after
        if valid_after is not None and claims.issued_at < valid_after.timestamp():
            raise AuthError("invalid_token", INVALID_SESSION)
        return self._result(user, with_tokens=True)

    async def logout(self) -> None:
        principal = current_principal(self.request)
        await self.repository.revoke_tokens(principal.user_id)
        await self.events.append(
            AuditEntry(
                action=AuditAction.LOGOUT,
                entity=AuditEntity.SESSION,
                entity_id=str(principal.user_id),
                entity_label=principal.email,
                summary=f"{principal.full_name} cerró sesión",
            )
        )

    async def session(self) -> AuthResult:
        principal = current_principal(self.request)
        user = await self.repository.get_for_session(principal.user_id)
        if not self._usable(user):
            raise AuthError("invalid_token", INVALID_SESSION)
        return self._result(user, with_tokens=False)

    async def update_profile(self, changes: dict) -> AuthResult:
        """Own profile. The target is always the token's user, never an id from the request."""
        principal = current_principal(self.request)
        if changes.get("full_name") is None:
            changes.pop("full_name", None)
        if changes:
            await self.repository.update(principal.user_id, changes)
        return await self.session()

    async def change_password(self, current: str, new: str) -> AuthResult:
        principal = current_principal(self.request)
        ip = self._ip()
        wait = await self.throttle.retry_after(principal.email, ip)
        if wait:
            raise AuthError("rate_limited", RATE_LIMITED, retry_after=wait)
        user = await self.repository.get_for_session(principal.user_id)
        if not self._usable(user):
            raise AuthError("invalid_token", INVALID_SESSION)
        if not verify_password(current, user.password_hash):
            await self.throttle.register_failure(principal.email, ip)
            raise AuthError(
                "wrong_current_password",
                "La contraseña actual no es correcta.",
                field="current_password",
            )
        problem = password_problem(new)
        if problem:
            raise AuthError("weak_password", problem, field="new_password")
        if new == current:
            raise AuthError(
                "weak_password",
                "La nueva contraseña debe ser distinta de la actual.",
                field="new_password",
            )
        await self.repository.set_password(user, hash_password(new), must_change=False)
        await self.repository.revoke_tokens(user.id)
        await self.repository.emit_sensitive(
            user, f"{user.full_name} cambió su contraseña", "password", "Contraseña", "cambiada"
        )
        return self._result(await self._reload(user), with_tokens=True)

    async def _reload(self, user: AppUser) -> AppUser:
        fresh = await self.repository.get_for_session(user.id)
        return fresh or user
