"""Constants of the tickets module. The string values are part of the API contract."""

from __future__ import annotations

import re
from enum import StrEnum


class FieldType(StrEnum):
    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    INTEGER = "integer"
    MONEY = "money"
    SELECT = "select"
    DATE = "date"
    TIME = "time"
    CHECKBOX = "checkbox"
    CHECKBOX_GROUP = "checkbox_group"
    FILE = "file"


class FieldRole(StrEnum):
    """What a field means, so totals and numbering do not depend on how it is named."""

    AMOUNT = "amount"
    CURRENCY = "currency"
    ACCOUNTING_DATE = "accounting_date"
    AIRCRAFT = "aircraft"
    AIRCRAFT_BRAND = "aircraft_brand"
    AIRCRAFT_MODEL = "aircraft_model"
    ITEM = "item"
    QUANTITY = "quantity"
    UNIT = "unit"
    SUPPLIER = "supplier"
    EXCHANGE_RATE = "exchange_rate"


# Field types that can carry each role.
ROLE_FIELD_TYPES: dict[FieldRole, frozenset[FieldType]] = {
    FieldRole.AMOUNT: frozenset({FieldType.MONEY, FieldType.NUMBER}),
    FieldRole.CURRENCY: frozenset({FieldType.SELECT}),
    FieldRole.ACCOUNTING_DATE: frozenset({FieldType.DATE}),
    FieldRole.AIRCRAFT: frozenset({FieldType.SELECT, FieldType.TEXT}),
    FieldRole.AIRCRAFT_BRAND: frozenset({FieldType.SELECT}),
    FieldRole.AIRCRAFT_MODEL: frozenset({FieldType.SELECT}),
    FieldRole.ITEM: frozenset({FieldType.SELECT, FieldType.TEXT}),
    FieldRole.QUANTITY: frozenset({FieldType.NUMBER, FieldType.INTEGER}),
    FieldRole.EXCHANGE_RATE: frozenset({FieldType.NUMBER}),
    FieldRole.UNIT: frozenset({FieldType.SELECT}),
    FieldRole.SUPPLIER: frozenset({FieldType.TEXT}),
}


class FieldWidth(StrEnum):
    FULL = "full"
    HALF = "half"


class StatusColor(StrEnum):
    DEFAULT = "default"
    INFO = "info"
    WARNING = "warning"
    DANGER = "danger"
    SUCCESS = "success"


class RelationKind(StrEnum):
    AIRCRAFT = "aircraft"
    SERVICE = "service"
    AIRPORT = "airport"
    INVENTORY = "inventory"
    TICKET = "ticket"


class DecisionValue(StrEnum):
    DONE = "done"
    REJECTED = "rejected"
    NOT_APPLICABLE = "not_applicable"


DECISION_LABELS = {
    DecisionValue.DONE.value: "Cumplido",
    DecisionValue.REJECTED.value: "Rechazado",
    DecisionValue.NOT_APPLICABLE.value: "No aplica",
}


class RuleField(StrEnum):
    AMOUNT_BASE = "amount_base"
    ATTACHMENTS = "attachments"
    CONCEPT = "concept"
    AMOUNT_VS_ROUTE = "amount_vs_route"


class RuleOperator(StrEnum):
    GT = "gt"
    LT = "lt"
    EQ = "eq"
    MISSING = "missing"


class RuleAction(StrEnum):
    WARN = "warn"
    REQUIRE_SUPPORT = "require_support"
    REQUIRE_AUTHORIZATION = "require_authorization"


# Operators each rule field understands.
RULE_FIELD_OPERATORS: dict[RuleField, frozenset[RuleOperator]] = {
    RuleField.AMOUNT_BASE: frozenset({RuleOperator.GT, RuleOperator.LT, RuleOperator.EQ}),
    RuleField.ATTACHMENTS: frozenset({RuleOperator.MISSING}),
    RuleField.CONCEPT: frozenset({RuleOperator.EQ}),
    RuleField.AMOUNT_VS_ROUTE: frozenset({RuleOperator.GT}),
}


class ExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    UNREADABLE = "unreadable"


class Confidence(StrEnum):
    LOW = "low"
    HIGH = "high"


class ExtractionSource(StrEnum):
    #: Reading submitted through the API by a person.
    MANUAL = "manual"
    #: Reading produced by a machine integration (attachments / OCR module).
    READER = "reader"


# Permissions.
PERM_VIEW = "tickets.view"
PERM_CREATE = "tickets.create"
PERM_EDIT = "tickets.edit"
PERM_DECIDE_ANY = "tickets.decide_any"
PERM_MANAGE_TYPES = "ticket_types.manage"

# Money.
BASE_CURRENCY = "USD"
CURRENCY_CATALOG = "currencies"
AMOUNT_MAX = 10**12
#: Whole-number fields (passenger counts and the like): far above any real value, below overflow.
INTEGER_MAX = 10**6
RATE_TOLERANCE = 1e-9
MONEY_PLACES = 2
RATE_PLACES = 12

# Numbering.
TICKET_CODE_PREFIX = "TK"
TICKET_CODE_PAD = 4
OPERATION_SEQUENCE_PAD = 3
COUNTER_TICKET_SERIES = "tk"
COUNTER_CONTROL_SERIES = "ctl"
CONTROL_NUMBER_PREFIX = "TVOA N°"
CONTROL_NUMBER_PAD = 4
#: Key of the read-only field of a root type that receives the control number.
CONTROL_NUMBER_FIELD = "control_number"
REGISTRATION_PATTERN = re.compile(r"^[A-Z0-9]{2,12}$")

# Automatic technical report created with every operation.
AUTO_REPORT_TYPE_CODE = "technical_report"
AUTO_REPORT_ROLE_CODE = "pilot"
AUTO_REPORT_TITLE = "Reporte técnico · {code}"
AUTO_REPORT_PAYLOAD = {"description": "Pendiente del piloto", "has_finding": False}

# Definition limits.
TYPE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
ROUTE_PATTERN = re.compile(r"^[A-Z0-9]{2,8}-[A-Z0-9]{2,8}$")
MAX_FIELDS = 120
MAX_STATUSES = 30
MAX_STEPS = 30
MAX_OPTIONS = 200
MAX_CHILD_TYPES = 30
TEXT_MAX = 500
TEXTAREA_MAX = 5000
CHECKBOX_GROUP_MAX = 50
PAYLOAD_MAX_KEYS = 200
COMMENT_MAX = 2000
OBSERVATION_MAX = 2000
AUTH_NOTE_MAX = 500
TITLE_MAX = 300
MAX_RELATIONS = 50
RULE_NAME_MAX = 160
RULE_MESSAGE_MAX = 500
ROUTE_LIMITS_MAX = 200

# Keys of the route of an operation, read by the route rule (`amount_vs_route`).
ROUTE_ORIGIN_KEY = "origin"
ROUTE_DESTINATION_KEY = "destination"
EXCHANGE_RATE_KEY = "exchange_rate"
AIRPORTS_CATALOG = "airports"
AIRCRAFTS_CATALOG = "aircrafts"

# Idempotency.
IDEMPOTENCY_HEADER = "idempotency-key"
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
IDEMPOTENCY_PENDING_SECONDS = 60
IDEMPOTENCY_MAX_BODY_BYTES = 32 * 1024 * 1024
IDEMPOTENCY_CODE = "idempotencia"
IDEMPOTENT_PATH_PREFIXES = ("/tickets/", "/rules/", "/ticket-types/", "/reconciliation/")
IDEMPOTENT_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
