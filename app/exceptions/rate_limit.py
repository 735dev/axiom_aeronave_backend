"""Throttling error: 429 with the seconds the caller must wait."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.exceptions.base import AppDomainError

RATE_LIMITED_MESSAGE = "Demasiadas solicitudes. Espera unos segundos antes de volver a intentar."


class RateLimitError(AppDomainError):
    STATUS_CODE_MAP = {"rate_limited": status.HTTP_429_TOO_MANY_REQUESTS}

    def __init__(self, retry_after: int, message: str = RATE_LIMITED_MESSAGE):
        super().__init__("rate_limited", message)
        self.retry_after = max(1, int(retry_after))

    def to_http(self) -> HTTPException:
        return HTTPException(
            status_code=self.http_status(),
            detail={"message": self.message},
            headers={"Retry-After": str(self.retry_after)},
        )
