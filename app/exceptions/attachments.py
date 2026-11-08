"""Errors of the attachments module."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.config.attachments import CODIGO_CODES
from app.exceptions.base import AppDomainError


class AttachmentError(AppDomainError):
    """An attachment that cannot be accepted, read or served.

    Codes listed in `CODIGO_CODES` travel in the body as `codigo`, as the contract defines.
    """

    STATUS_CODE_MAP = {
        "not_found": status.HTTP_404_NOT_FOUND,
        "validation": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "extension": status.HTTP_400_BAD_REQUEST,
        "vacio": status.HTTP_400_BAD_REQUEST,
        "tamano": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        "idempotencia": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "tipo": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "limite": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "campo": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "locked": status.HTTP_409_CONFLICT,
        "forbidden": status.HTTP_403_FORBIDDEN,
        "almacenamiento": status.HTTP_503_SERVICE_UNAVAILABLE,
        "unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    }

    def to_http(self) -> HTTPException:
        detail: dict = {"message": self.message}
        if self.field:
            detail["errors"] = {self.field: self.message}
        if self.code in CODIGO_CODES:
            detail["codigo"] = self.code
        return HTTPException(status_code=self.http_status(), detail=detail)


class NotificationError(AppDomainError):
    STATUS_CODE_MAP = {
        "not_found": status.HTTP_404_NOT_FOUND,
        "validation": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
