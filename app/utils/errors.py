"""Builders for the contract error body `{title, detail, errors}`."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from app.config.errors import (
    DEFAULT_ERROR_TITLE,
    ERROR_DETAILS,
    ERROR_TITLES,
    SERVER_ERROR_DETAIL,
    SERVER_ERROR_TITLE,
)


def error_body(
    status_code: int,
    detail: str | None = None,
    errors: dict[str, str] | None = None,
    title: str | None = None,
    codigo: str | None = None,
) -> dict[str, Any]:
    if status_code >= 500:
        title = title or SERVER_ERROR_TITLE
        detail = detail or SERVER_ERROR_DETAIL
    else:
        title = title or ERROR_TITLES.get(status_code, DEFAULT_ERROR_TITLE)
        detail = detail or ERROR_DETAILS.get(status_code, ERROR_DETAILS[400])
    body: dict[str, Any] = {"title": title, "detail": detail}
    if errors:
        body["errors"] = errors
    if codigo:
        body["codigo"] = codigo
    return body


def error_response(
    status_code: int,
    detail: str | None = None,
    errors: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    codigo: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(status_code, detail, errors, codigo=codigo),
        headers=headers,
    )
