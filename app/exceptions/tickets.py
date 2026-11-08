"""Errors of the tickets module."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.exceptions.base import AppDomainError


class TicketError(AppDomainError):
    """A business rule of tickets, ticket types or rules.

    `errors` maps a form key to its message so the client can mark the offending input.
    """

    STATUS_CODE_MAP = {
        "not_found": status.HTTP_404_NOT_FOUND,
        "validation": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "forbidden": status.HTTP_403_FORBIDDEN,
        "locked": status.HTTP_409_CONFLICT,
        "conflict": status.HTTP_409_CONFLICT,
        "protected": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "in_use": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }

    def __init__(
        self,
        code: str,
        message: str,
        field: str | None = None,
        errors: dict[str, str] | None = None,
    ):
        super().__init__(code, message, field)
        self.errors = errors or ({field: message} if field else {})

    def to_http(self) -> HTTPException:
        detail: dict = {"message": self.message}
        if self.errors:
            detail["errors"] = self.errors
        return HTTPException(status_code=self.http_status(), detail=detail)
