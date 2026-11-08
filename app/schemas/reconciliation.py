from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config.closing import MAX_JUSTIFICATION, PERIOD_FORMAT_MESSAGE, PERIOD_PATTERN
from app.schemas.common import RequestModel


def _period(value: str) -> str:
    if not PERIOD_PATTERN.match(value or ""):
        raise ValueError(PERIOD_FORMAT_MESSAGE)
    return value


class PeriodBody(RequestModel):
    period: str = Field(max_length=7)

    _period = field_validator("period")(_period)


class AcknowledgementBody(PeriodBody):
    key: str = Field(min_length=1, max_length=200)
    note: str = Field(max_length=MAX_JUSTIFICATION)


class AcknowledgementRead(BaseModel):
    note: str
    actor_id: str | None = None
    actor_name: str
    actor_role: str
    at: datetime

    model_config = ConfigDict(from_attributes=True)


class DifferenceRead(BaseModel):
    key: str
    kind: str
    severity: str
    reason: str
    ticket_id: str
    ticket_code: str
    operation_code: str
    file_id: str | None = None
    file_name: str | None = None
    expense_amount: float
    expense_currency: str
    receipt_amount: float | None = None
    receipt_currency: str | None = None
    acknowledgement: AcknowledgementRead | None = None

    model_config = ConfigDict(from_attributes=True)


class RowRead(BaseModel):
    ticket_id: str
    code: str
    operation_code: str
    title: str
    concept: str
    date: date
    status_name: str
    amount: float
    currency: str
    amount_base: float
    base_currency: str
    validated_receipts: int
    receipt_amount: float | None = None
    receipt_currency: str | None = None
    state: str
    difference_kinds: list[str]
    justification: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ReportTotalsRead(BaseModel):
    reconciled_base: float
    difference_base: float
    discarded_base: float


class ClosedPeriodRead(BaseModel):
    period: str
    closed_at: datetime
    closed_by_id: UUID | None = None
    closed_by_name: str
    closed_by_role: str
    rows_count: int
    total_base: float
    acknowledged_count: int

    model_config = ConfigDict(from_attributes=True)


class ReportRead(BaseModel):
    period: str
    start: date = Field(alias="from")
    end: date = Field(alias="to")
    base_currency: str
    rows: list[RowRead]
    differences: list[DifferenceRead]
    open_differences: int
    totals: ReportTotalsRead
    close: ClosedPeriodRead | None = None

    model_config = ConfigDict(populate_by_name=True)


class ExportReceiptRead(BaseModel):
    period: str
    rows: int
