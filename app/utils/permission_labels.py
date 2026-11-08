"""Readable labels for permission changes, so the trail does not show bare codes."""

from __future__ import annotations

from app.models.access import Permission

GRANTED_LABEL = "concedido"
REVOKED_LABEL = "revocado"


def permission_label(permission: Permission) -> str:
    return f"{permission.name} ({permission.code})"
