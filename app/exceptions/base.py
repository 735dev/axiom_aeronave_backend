"""Project base for domain errors: a basekit DomainError that can name a form field."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi_basekit.exceptions.domain import DomainError


class AppDomainError(DomainError):
    """DomainError with an optional field so the client can mark the offending input.

    `to_http()` keeps the basekit contract (an HTTPException) and carries the field
    in a dict detail that the exception handlers turn into `{title, detail, errors}`.
    """

    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(code, message)
        self.field = field

    def to_http(self) -> HTTPException:
        detail: dict = {"message": self.message}
        if self.field:
            detail["errors"] = {self.field: self.message}
        return HTTPException(status_code=self.http_status(), detail=detail)
