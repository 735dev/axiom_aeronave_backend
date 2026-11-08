"""Constants of the notifications module. Kinds are part of the API contract."""

from __future__ import annotations

from enum import StrEnum


class NotificationKind(StrEnum):
    OPERATION_CREATED = "operation_created"
    TICKET_ASSIGNED = "ticket_assigned"
    NAVAID_PENDING = "navaid_pending"
    CLOSE_BLOCKED = "close_blocked"
    TECHNICAL_REPORT = "technical_report"
    RECEIPT_VALIDATION = "receipt_validation"
    OPERATION_CLOSED = "operation_closed"


#: Two equal notices about the same ticket inside this window are one notice.
DEDUPE_WINDOW_SECONDS = 5 * 60
TITLE_MAX = 200
BODY_MAX = 600

# Role codes the events address (stable; the id changes if a role is recreated).
ROLE_OPERATIONS = "operations"
ROLE_MAINTENANCE = "maintenance"
ROLE_ADMIN = "admin"
ROLE_PILOT = "pilot"
ROLE_GROUND = "ground"
CREW_ROLES = (ROLE_PILOT, ROLE_GROUND)

# Ticket type codes the events react to.
TYPE_INAC = "inac_permit"
TYPE_TECHNICAL_REPORT = "technical_report"

NO_OPERATION = "sin operación"
NO_ROLE = "sin rol"
