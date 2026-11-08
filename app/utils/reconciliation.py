"""Cross expenses with their validated receipts for one month.

Port of the reference `computeReconciliation`. It finds the three differences of the
scope: expense without support, support without expense and amount mismatch. Pure: the
repository supplies the facts, nothing here reads the database.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.config.closing import (
    AMOUNT_TOLERANCE,
    HIGH_SEVERITY_GAP,
    DifferenceKind,
    DifferenceReason,
    RowState,
    Severity,
)

_CENT = Decimal("0.01")
_TOLERANCE = Decimal(str(AMOUNT_TOLERANCE))
_GAP = Decimal(str(HIGH_SEVERITY_GAP))


def round2(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def same_amount(a: Decimal, b: Decimal) -> bool:
    return abs(round2(a - b)) <= _TOLERANCE


def period_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def period_bounds(period: str) -> tuple[date, date]:
    year, month = (int(part) for part in period.split("-"))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


@dataclass
class Receipt:
    """A validated reading of a receipt: the only kind that counts as support."""

    file_id: str
    file_name: str
    amount: Decimal
    currency: str


@dataclass
class Expense:
    ticket_id: str
    code: str
    title: str
    concept: str
    operation_id: str | None
    operation_code: str
    accounting_date: date
    status_name: str
    committed: bool
    amount: Decimal
    currency: str
    amount_base: Decimal
    base_currency: str
    #: Supports loaded on the expense (readings, and files once the attachments land).
    supports: int
    #: At least one reading exists, validated or not.
    has_reading: bool
    receipts: list[Receipt] = field(default_factory=list)


@dataclass
class Acknowledgement:
    note: str
    actor_id: str | None
    actor_name: str
    actor_role: str
    at: datetime


@dataclass
class Difference:
    key: str
    kind: DifferenceKind
    severity: Severity
    reason: DifferenceReason
    ticket_id: str
    ticket_code: str
    operation_code: str
    expense_amount: Decimal
    expense_currency: str
    file_id: str | None = None
    file_name: str | None = None
    receipt_amount: Decimal | None = None
    receipt_currency: str | None = None
    acknowledgement: Acknowledgement | None = None


@dataclass
class Row:
    ticket_id: str
    code: str
    operation_code: str
    title: str
    concept: str
    date: date
    status_name: str
    amount: Decimal
    currency: str
    amount_base: Decimal
    base_currency: str
    validated_receipts: int
    state: RowState
    receipt_amount: Decimal | None = None
    receipt_currency: str | None = None
    difference_kinds: list[DifferenceKind] = field(default_factory=list)
    justification: str | None = None


@dataclass
class Report:
    period: str
    start: date
    end: date
    base_currency: str
    rows: list[Row]
    differences: list[Difference]
    open_differences: int
    reconciled_base: Decimal
    difference_base: Decimal
    discarded_base: Decimal


def compute(
    period: str,
    expenses: list[Expense],
    acknowledgements: dict[str, Acknowledgement],
    base_currency: str,
) -> Report:
    """`expenses` holds every expense with money the caller may see, from any month: a
    receipt loaded on an operation can match an expense of another month."""
    start, end = period_bounds(period)
    in_period = [e for e in expenses if period_of(e.accounting_date) == period]
    differences: list[Difference] = []
    kinds_by_ticket: dict[str, list[DifferenceKind]] = {}

    def push(expense: Expense, kind, severity, reason, receipt: Receipt | None = None) -> None:
        file_id = receipt.file_id if receipt else None
        key = f"{kind.value}:{expense.ticket_id}:{file_id or ''}"
        differences.append(
            Difference(
                key=key,
                kind=kind,
                severity=severity,
                reason=reason,
                ticket_id=expense.ticket_id,
                ticket_code=expense.code,
                operation_code=expense.operation_code,
                expense_amount=expense.amount,
                expense_currency=expense.currency,
                file_id=file_id,
                file_name=receipt.file_name if receipt else None,
                receipt_amount=receipt.amount if receipt else None,
                receipt_currency=receipt.currency if receipt else None,
                acknowledgement=acknowledgements.get(key),
            )
        )
        bucket = kinds_by_ticket.setdefault(expense.ticket_id, [])
        if kind not in bucket:
            bucket.append(kind)

    def siblings_of(expense: Expense) -> list[Expense]:
        if expense.operation_id:
            return [e for e in expenses if e.committed and e.operation_id == expense.operation_id]
        return [e for e in expenses if e.committed and e.ticket_id == expense.ticket_id]

    for expense in in_period:
        if not expense.committed:
            # A discarded expense is not waiting for a receipt, but a validated receipt on
            # it is a support left without a live expense.
            for receipt in expense.receipts:
                push(
                    expense,
                    DifferenceKind.SUPPORT_WITHOUT_EXPENSE,
                    Severity.MEDIUM,
                    DifferenceReason.DISCARDED_EXPENSE,
                    receipt,
                )
            continue

        if expense.supports == 0:
            push(
                expense,
                DifferenceKind.EXPENSE_WITHOUT_SUPPORT,
                Severity.HIGH,
                DifferenceReason.NO_FILES,
            )
        elif not expense.receipts and expense.has_reading:
            # A reading exists but nobody answered for it: still a proposal, not support.
            push(
                expense,
                DifferenceKind.EXPENSE_WITHOUT_SUPPORT,
                Severity.MEDIUM,
                DifferenceReason.UNVALIDATED,
            )

        siblings = siblings_of(expense)
        for receipt in expense.receipts:
            if receipt.currency != expense.currency:
                push(
                    expense,
                    DifferenceKind.AMOUNT_MISMATCH,
                    Severity.MEDIUM,
                    DifferenceReason.CURRENCY,
                    receipt,
                )
            elif not same_amount(receipt.amount, expense.amount):
                gap = abs(receipt.amount - expense.amount) / max(expense.amount, _TOLERANCE)
                push(
                    expense,
                    DifferenceKind.AMOUNT_MISMATCH,
                    Severity.HIGH if gap >= _GAP else Severity.MEDIUM,
                    DifferenceReason.AMOUNT,
                    receipt,
                )
            if not any(
                s.currency == receipt.currency and same_amount(s.amount, receipt.amount)
                for s in siblings
            ):
                push(
                    expense,
                    DifferenceKind.SUPPORT_WITHOUT_EXPENSE,
                    Severity.MEDIUM,
                    DifferenceReason.NO_MATCHING_EXPENSE,
                    receipt,
                )

    rows: list[Row] = []
    for expense in in_period:
        kinds = kinds_by_ticket.get(expense.ticket_id, [])
        notes = [
            d.acknowledgement.note
            for d in differences
            if d.ticket_id == expense.ticket_id and d.acknowledgement
        ]
        state = (
            RowState.DIFFERENCE
            if kinds
            else RowState.RECONCILED if expense.committed else RowState.DISCARDED
        )
        first = expense.receipts[0] if expense.receipts else None
        rows.append(
            Row(
                ticket_id=expense.ticket_id,
                code=expense.code,
                operation_code=expense.operation_code,
                title=expense.title,
                concept=expense.concept,
                date=expense.accounting_date,
                status_name=expense.status_name,
                amount=expense.amount,
                currency=expense.currency,
                amount_base=expense.amount_base,
                base_currency=expense.base_currency,
                validated_receipts=len(expense.receipts),
                state=state,
                receipt_amount=first.amount if first else None,
                receipt_currency=first.currency if first else None,
                difference_kinds=list(kinds),
                justification=" | ".join(notes) if notes else None,
            )
        )

    def total(state: RowState) -> Decimal:
        return round2(sum((r.amount_base for r in rows if r.state == state), Decimal(0)))

    return Report(
        period=period,
        start=start,
        end=end,
        base_currency=base_currency,
        rows=rows,
        differences=differences,
        open_differences=sum(1 for d in differences if d.acknowledgement is None),
        reconciled_base=total(RowState.RECONCILED),
        difference_base=total(RowState.DIFFERENCE),
        discarded_base=total(RowState.DISCARDED),
    )


def csv_rows(report: Report) -> list[list[Any]]:
    """Plain values of the detail, in the column order of `CSV_HEADERS`."""
    from app.config.closing import CSV_KIND_LABELS, CSV_STATE_LABELS

    return [
        [
            r.code,
            r.operation_code,
            r.title,
            r.concept,
            r.date.isoformat(),
            r.status_name,
            r.amount,
            r.currency,
            r.amount_base,
            str(r.validated_receipts),
            r.receipt_amount,
            r.receipt_currency or "",
            CSV_STATE_LABELS[r.state.value],
            ", ".join(CSV_KIND_LABELS[k.value] for k in r.difference_kinds),
            r.justification or "",
        ]
        for r in report.rows
    ]
