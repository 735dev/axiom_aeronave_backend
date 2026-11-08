"""Errors of the administration modules (roles, users, catalogs, modules)."""

from __future__ import annotations

from fastapi import status

from app.exceptions.base import AppDomainError


class AdminError(AppDomainError):
    STATUS_CODE_MAP = {
        "not_found": status.HTTP_404_NOT_FOUND,
        "validation": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "duplicate": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "protected": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "in_use": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "privilege": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }


class AuditError(AppDomainError):
    STATUS_CODE_MAP = {"append_only": status.HTTP_500_INTERNAL_SERVER_ERROR}
