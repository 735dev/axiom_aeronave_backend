"""Exception handlers: every failure leaves as `{title, detail, errors?}`.

Nothing from an exception message, a traceback or the database ever reaches the
client. The detail goes to the log only.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi_basekit.exceptions.api_exceptions import APIException
from fastapi_basekit.exceptions.domain import DomainError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config.errors import (
    DEFAULT_VALIDATION_MESSAGE,
    EMAIL_MESSAGE,
    ERROR_DETAILS,
    JSON_INVALID_TYPES,
    VALIDATION_MESSAGES,
)
from app.exceptions.auth import AuthError
from app.exceptions.rate_limit import RateLimitError
from app.utils.errors import error_response

logger = logging.getLogger(__name__)

CONFLICT_DETAIL = "El registro ya existe o entra en conflicto con otro."


def _is_starlette_default(status_code: int, detail: Any) -> bool:
    try:
        return detail == HTTPStatus(status_code).phrase
    except ValueError:
        return False


def _auth_headers(status_code: int) -> dict[str, str] | None:
    return {"WWW-Authenticate": "Bearer"} if status_code == 401 else None


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail: str | None
    errors: dict[str, str] | None = None
    codigo: str | None = None
    if isinstance(exc.detail, dict):
        detail = exc.detail.get("message")
        errors = exc.detail.get("errors")
        codigo = exc.detail.get("codigo")
    elif isinstance(exc.detail, str) and not _is_starlette_default(exc.status_code, exc.detail):
        detail = exc.detail
    else:
        detail = None
    headers = dict(getattr(exc, "headers", None) or {}) or _auth_headers(exc.status_code)
    return error_response(exc.status_code, detail, errors, headers, codigo)


async def api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
    if exc.status >= 500:
        logger.error("api exception %s: %s", exc.status_code, exc.message)
    # A 404 never echoes the id the caller asked for.
    detail = None if exc.status == 404 else exc.message
    return error_response(exc.status, detail, None, _auth_headers(exc.status))


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    """Safety net for a DomainError that escapes a controller: same mapping as to_http()."""
    http_exc = exc.to_http()
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(exc, (AuthError, RateLimitError)) and retry_after:
        http_exc.headers = {"Retry-After": str(retry_after)}
    return await http_exception_handler(request, http_exc)


def _field_name(loc: tuple) -> str:
    names = [str(part) for part in loc if part not in ("body", "query", "path", "header")]
    return str(names[-1]) if names else "body"


def validation_errors(errors: list[dict[str, Any]]) -> dict[str, str]:
    """Map pydantic errors to `{field: message}`; the client key equals the form key."""
    mapped: dict[str, str] = {}
    for error in errors:
        message = VALIDATION_MESSAGES.get(error.get("type", ""), DEFAULT_VALIDATION_MESSAGE)
        raw = str(error.get("msg", ""))
        if "email address" in raw:
            message = EMAIL_MESSAGE
        elif error.get("type") == "value_error":
            message = raw.removeprefix("Value error, ") or message
        mapped.setdefault(_field_name(tuple(error.get("loc", ()))), message)
    return mapped


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if any(e.get("type") in JSON_INVALID_TYPES for e in errors):
        return error_response(400, ERROR_DETAILS[400])
    mapped = validation_errors(errors)
    detail = "Revisa los campos marcados: " + ", ".join(mapped) + "."
    return error_response(422, detail, mapped)


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning("integrity error on %s %s: %s", request.method, request.url.path, exc.orig)
    return error_response(422, CONFLICT_DETAIL)


async def unhandled_exception_response(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except ImportError:
        pass
    return error_response(500)
