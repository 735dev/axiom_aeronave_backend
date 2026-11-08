"""Authentication and permission enforcement, the single gate in front of every route.

For each request it resolves the route in the permission registry and then:
- public route: lets it through;
- anything else: verifies the bearer token, loads the user, checks revocation and
  that user and role are active, and (for `permission` routes) that the effective
  permissions satisfy the registry. 401 when the caller is not authenticated, 403 when
  authenticated but not allowed. Any failure while deciding denies (fail closed).

It also binds the audit actor from the verified token, so audit events can never take
the actor from a request body.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import database
from app.config.access import (
    PASSWORD_CHANGE_REQUIRED_CODE,
    PASSWORD_CHANGE_REQUIRED_MESSAGE,
    PASSWORD_PENDING_ALLOWED,
)
from app.config.auth import BEARER_SCHEME, TokenType
from app.config.settings import get_settings
from app.repositories.user import UserRepository
from app.utils.audit_context import Actor, Origin, bound_request
from app.utils.client_ip import client_ip
from app.utils.effective_permissions import effective_codes, has_permission
from app.utils.endpoint_registry import Access, RouteMatcher, RouteRule
from app.utils.errors import error_response
from app.utils.exception_handlers import unhandled_exception_response
from app.utils.principal import Principal
from app.utils.tokens import TokenService

logger = logging.getLogger(__name__)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != BEARER_SCHEME or not token.strip():
        return None
    return token.strip()


class AccessMiddleware:
    def __init__(self, app: ASGIApp, fastapi_app: FastAPI):
        self.app = app
        self._fastapi_app = fastapi_app
        self._matcher: RouteMatcher | None = None
        self._tokens = TokenService()
        prefix = get_settings().API_V1_STR
        self._pending_allowed = frozenset(
            (method, prefix + path) for method, path in PASSWORD_PENDING_ALLOWED
        )

    def _rule_for(self, method: str, path: str) -> RouteRule | None:
        if self._matcher is None:
            self._matcher = RouteMatcher(self._fastapi_app)
        return self._matcher.match(method, path)

    async def _authenticate(self, request: Request) -> tuple[Principal, Actor] | None:
        token = _bearer_token(request)
        if token is None:
            return None
        claims = self._tokens.read(token, TokenType.ACCESS)
        if claims is None:
            return None
        async with database.AsyncSessionFactory() as session:
            user = await UserRepository(session).get_for_session(claims.user_id)
            if user is None or not user.is_active or not user.role.is_active:
                return None
            valid_after = user.tokens_valid_after
            if valid_after is not None and claims.issued_at < valid_after.timestamp():
                return None
            principal = Principal(
                user_id=user.id,
                email=user.email,
                full_name=user.full_name,
                role_id=user.role_id,
                role_code=user.role.code,
                role_name=user.role.name,
                permissions=effective_codes(user),
                token_issued_at=claims.issued_at,
                must_change_password=user.must_change_password,
            )
        return principal, Actor(str(principal.user_id), principal.full_name, principal.role_name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        origin = Origin(ip=client_ip(request), user_agent=request.headers.get("user-agent"))
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            rule = self._rule_for(scope["method"], scope["path"])
            actor: Actor | None = None
            if rule is not None and rule.access is not Access.PUBLIC:
                authenticated = await self._authenticate(request)
                if authenticated is None:
                    logger.warning(
                        "authn_failed method=%s path=%s ip=%s",
                        scope["method"],
                        scope["path"],
                        origin.ip,
                    )
                    response = error_response(401, headers={"WWW-Authenticate": "Bearer"})
                    await response(scope, receive, tracking_send)
                    return
                principal, actor = authenticated
                if (
                    principal.must_change_password
                    and (
                        scope["method"],
                        scope["path"],
                    )
                    not in self._pending_allowed
                ):
                    logger.warning(
                        "password_change_required user=%s method=%s path=%s ip=%s",
                        principal.user_id,
                        scope["method"],
                        scope["path"],
                        origin.ip,
                    )
                    response = error_response(
                        403,
                        PASSWORD_CHANGE_REQUIRED_MESSAGE,
                        codigo=PASSWORD_CHANGE_REQUIRED_CODE,
                    )
                    await response(scope, receive, tracking_send)
                    return
                if rule.access is Access.PERMISSION and not has_permission(
                    principal.permissions, rule.permissions, rule.prefix_match
                ):
                    logger.warning(
                        "authz_denied user=%s method=%s path=%s ip=%s",
                        principal.user_id,
                        scope["method"],
                        scope["path"],
                        origin.ip,
                    )
                    await error_response(403)(scope, receive, tracking_send)
                    return
                scope.setdefault("state", {})["principal"] = principal
            with bound_request(actor, origin):
                await self.app(scope, receive, tracking_send)
        except Exception as exc:  # noqa: BLE001 - last line of defence: never leak, never open
            if started:
                raise
            response = await unhandled_exception_response(request, exc)
            await response(scope, receive, tracking_send)
