"""Errors of the authentication flow."""

from __future__ import annotations

from fastapi import status

from app.exceptions.base import AppDomainError


class AuthError(AppDomainError):
    STATUS_CODE_MAP = {
        "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
        "invalid_token": status.HTTP_401_UNAUTHORIZED,
        "rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
        "weak_password": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "wrong_current_password": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "invalid_signature": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }

    def __init__(
        self, code: str, message: str, field: str | None = None, retry_after: int | None = None
    ):
        super().__init__(code, message, field)
        self.retry_after = retry_after
