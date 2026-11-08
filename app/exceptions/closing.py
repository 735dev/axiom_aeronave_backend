"""Errors of the signed closing, reconciliation and passengers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.config.closing import PERIOD_CLOSED_CODE
from app.exceptions.base import AppDomainError
from app.exceptions.tickets import TicketError


class ClosedPeriodError(TicketError):
    """The accounting period of an expense is closed: nothing in it changes any more."""

    def __init__(self, message: str):
        super().__init__("locked", message)

    def to_http(self) -> HTTPException:
        return HTTPException(
            status_code=self.http_status(),
            detail={"message": self.message, "codigo": PERIOD_CLOSED_CODE},
        )


class ReconciliationError(AppDomainError):
    """A rule of the reconciliation (period format, justification, close)."""

    STATUS_CODE_MAP = {
        "not_found": status.HTTP_404_NOT_FOUND,
        "validation": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "conflict": status.HTTP_409_CONFLICT,
    }

    def __init__(
        self,
        code: str,
        message: str,
        field: str | None = None,
        codigo: str | None = None,
        errors: dict[str, str] | None = None,
    ):
        super().__init__(code, message, field)
        self.codigo = codigo
        self.errors = errors or ({field: message} if field else {})

    def to_http(self) -> HTTPException:
        detail: dict[str, Any] = {"message": self.message}
        if self.errors:
            detail["errors"] = self.errors
        if self.codigo:
            detail["codigo"] = self.codigo
        return HTTPException(status_code=self.http_status(), detail=detail)


class PassengerError(AppDomainError):
    STATUS_CODE_MAP = {
        "not_found": status.HTTP_404_NOT_FOUND,
        "validation": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "forbidden": status.HTTP_403_FORBIDDEN,
    }
