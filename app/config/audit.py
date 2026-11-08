"""Audit vocabulary. The values are part of the API contract (AuditAction / AuditEntity)."""

from __future__ import annotations

from enum import StrEnum


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    STATUS_CHANGE = "status_change"
    DECISION = "decision"
    COMMENT = "comment"
    ATTACH = "attach"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    DENIED = "denied"
    PERMISSION_CHANGE = "permission_change"
    EXPORT = "export"


class AuditEntity(StrEnum):
    TICKET = "ticket"
    TICKET_TYPE = "ticket_type"
    USER = "user"
    ROLE = "role"
    MODULE = "module"
    CATALOG = "catalog"
    RECONCILIATION = "reconciliation"
    SESSION = "session"
    NOTIFICATION = "notification"
    AUDIT = "audit"
    PASSENGER = "passenger"


# Singular noun with article, used to compose the Spanish summary of a diff.
ENTITY_SUBJECT = {
    AuditEntity.TICKET: "el ticket",
    AuditEntity.TICKET_TYPE: "el tipo de ticket",
    AuditEntity.USER: "al usuario",
    AuditEntity.ROLE: "el rol",
    AuditEntity.MODULE: "el módulo",
    AuditEntity.CATALOG: "el registro de catálogo",
    AuditEntity.RECONCILIATION: "la conciliación",
    AuditEntity.SESSION: "la sesión",
    AuditEntity.NOTIFICATION: "el aviso",
    AuditEntity.AUDIT: "la bitácora",
    AuditEntity.PASSENGER: "el pasajero",
}

SYSTEM_ACTOR_ID = "system"
SYSTEM_ACTOR_NAME = "Sistema"
SYSTEM_ACTOR_ROLE = "Automático"

ANONYMOUS_ACTOR_ID = "anonymous"
ANONYMOUS_ACTOR_ROLE = "Sin sesión"
ANONYMOUS_ACTOR_FALLBACK_NAME = "Desconocido"

USER_AGENT_MAX_LENGTH = 255
EMAIL_MAX_LENGTH = 255

EXPORT_FILENAME = "bitacora-{day}.csv"
EXPORT_HEADERS = (
    "Secuencia",
    "Fecha (UTC)",
    "Actor",
    "Rol del actor",
    "Acción",
    "Entidad",
    "Id de la entidad",
    "Etiqueta",
    "Resumen",
    "Cambios",
    "IP",
)
EXPORT_TOO_MANY_MESSAGE = (
    "La exportación supera el máximo de {limit} eventos. Acota el rango de fechas o los filtros."
)
