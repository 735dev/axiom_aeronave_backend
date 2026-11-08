"""Constants of the access-control model."""

from __future__ import annotations

from enum import StrEnum

SUPERUSER_ROLE_CODE = "superuser"
SEED_ALL_PERMISSIONS = "*"

# Derived permissions are generated per ticket type: tickets.<action>.<type_code>.
TYPE_PERMISSION_ACTIONS = (
    ("tickets.view", "Ver"),
    ("tickets.create", "Crear"),
    ("tickets.edit", "Editar"),
)
TICKETS_MODULE_CODE = "tickets"


class OverrideEffect(StrEnum):
    GRANT = "grant"
    REVOKE = "revoke"


class PermissionOrigin(StrEnum):
    ROLE = "role"
    GRANTED = "granted"
    REVOKED = "revoked"


# Module fields an administrator may change from the API.
MODULE_EDITABLE_FIELDS = (
    "name",
    "description",
    "route",
    "icon",
    "menu_permission",
    "show_in_menu",
    "sort_order",
    "is_active",
)


#: Routes (method, path below the API prefix) a user flagged `must_change_password` may call:
#: change the password, read the own session (the client needs the flag) and log out.
PASSWORD_PENDING_ALLOWED = frozenset(
    {
        ("POST", "/auth/password/"),
        ("GET", "/auth/me/"),
        ("POST", "/auth/logout/"),
    }
)
PASSWORD_CHANGE_REQUIRED_MESSAGE = "Debes cambiar tu contraseña temporal antes de continuar."
PASSWORD_CHANGE_REQUIRED_CODE = "cambio_contrasena"
