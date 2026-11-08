"""Constants of the signed closing, passengers, reconciliation and totals. The string values
are part of the API contract."""

from __future__ import annotations

import re
from enum import StrEnum

# --- signed closing ------------------------------------------------------------------

#: The step of the operation whose `done` decision is the signature of the closing.
SIGNATURE_STEP_CODE = "signature"
#: Document type reported for a support the inventory cannot classify.
DEFAULT_DOCUMENT_TYPE = "comprobante"


class BlockerKind(StrEnum):
    PENDING_STEP = "pending_step"
    UNRESOLVED_CHILD = "unresolved_child"
    EXPENSE_WITHOUT_SUPPORT = "expense_without_support"
    RECEIPT_AWAITING_VALIDATION = "receipt_awaiting_validation"


#: Blockers the closing act itself resolves or that the core already reports on its own.
BLOCKERS_SKIPPED_BY_CLOSE_CHECK = frozenset(
    {BlockerKind.PENDING_STEP, BlockerKind.UNRESOLVED_CHILD}
)

NO_RESPONSIBLE_ROLE = "Sin responsable"

CLOSE_NO_SUPPORT_MESSAGE = "No se puede cerrar: {codes} no tiene(n) soporte cargado."
CLOSE_AWAITING_MESSAGE = "No se puede cerrar: hay comprobantes sin validar ({codes})."
CLOSE_NO_SIGNATURE_DECISION = "No se puede cerrar: falta la firma de cierre de quien corresponda."
CLOSE_NO_SIGNATURE_IMAGE = (
    "No se puede cerrar: quien firma debe cargar su firma en su perfil y volver a firmar."
)

# --- reconciliation ------------------------------------------------------------------

PERM_ANALYTICS = "analytics.view"
PERM_RECONCILIATION_CLOSE = "reconciliation.close"

PERIOD_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PERIOD_FORMAT_MESSAGE = "El período debe tener el formato AAAA-MM."
MIN_JUSTIFICATION = 5
MAX_JUSTIFICATION = 1000
#: Money tolerance: one cent. Beyond that it is a real difference.
AMOUNT_TOLERANCE = 0.01
#: Gap (relative to the expense) from which a mismatch is high severity.
HIGH_SEVERITY_GAP = 0.10


class DifferenceKind(StrEnum):
    EXPENSE_WITHOUT_SUPPORT = "expense_without_support"
    SUPPORT_WITHOUT_EXPENSE = "support_without_expense"
    AMOUNT_MISMATCH = "amount_mismatch"


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"


class DifferenceReason(StrEnum):
    NO_FILES = "no_files"
    UNVALIDATED = "unvalidated"
    CURRENCY = "currency"
    AMOUNT = "amount"
    NO_MATCHING_EXPENSE = "no_matching_expense"
    DISCARDED_EXPENSE = "discarded_expense"


class RowState(StrEnum):
    RECONCILED = "reconciled"
    DIFFERENCE = "difference"
    DISCARDED = "discarded"


PERIOD_CLOSED_CODE = "periodo_cerrado"
PERIOD_CLOSED_MESSAGE = "El período {period} está cerrado: {label} no admite cambios."
PERIOD_ALREADY_CLOSED_MESSAGE = "El período {period} ya está cerrado."

#: Excel in a Spanish locale: the comma is the decimal separator.
CSV_SEPARATOR = ";"
CSV_BOM = "﻿"
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CSV_HEADERS = (
    "Gasto",
    "Operación",
    "Título",
    "Concepto",
    "Fecha contable",
    "Estado",
    "Monto",
    "Moneda",
    "Monto base",
    "Comprobantes validados",
    "Monto del comprobante",
    "Moneda del comprobante",
    "Conciliación",
    "Diferencias",
    "Justificación",
)
CSV_STATE_LABELS = {
    RowState.RECONCILED.value: "Conciliado",
    RowState.DIFFERENCE.value: "Con diferencia",
    RowState.DISCARDED.value: "Descartado",
}
CSV_KIND_LABELS = {
    DifferenceKind.EXPENSE_WITHOUT_SUPPORT.value: "Gasto sin soporte",
    DifferenceKind.SUPPORT_WITHOUT_EXPENSE.value: "Soporte sin gasto",
    DifferenceKind.AMOUNT_MISMATCH.value: "Monto no coincide",
}
CSV_FILENAME = "conciliacion-{period}.csv"

# --- passengers ----------------------------------------------------------------------

MAX_PASSENGER_RESULTS = 50
#: Below this a document query matches almost anything: the result would be useless.
MIN_DOCUMENT_QUERY = 3
MAX_PASSENGER_QUERY = 100
MAX_PASSENGERS_PER_MANIFEST = 500
PASSENGER_NAME_MAX = 200
PASSENGER_DOCUMENT_MAX = 40
PASSENGER_NATIONALITY_MAX = 60

# --- totals --------------------------------------------------------------------------

UNCLASSIFIED_KEY = "__sin__"
UNCLASSIFIED_LABEL = "Sin clasificar"

# --- quick log -----------------------------------------------------------------------

DEPARTURE_KEY = "actual_departure"
ARRIVAL_KEY = "actual_arrival"
NEXT_DAY_KEY = "arrival_next_day"
SAME_TIME_MESSAGE = "La salida y la llegada no pueden ser la misma hora."
ARRIVAL_BEFORE_DEPARTURE_MESSAGE = (
    "La llegada es anterior a la salida: marca «llegó al día siguiente» si cruzó la medianoche."
)
