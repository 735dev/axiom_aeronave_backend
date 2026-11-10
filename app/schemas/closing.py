from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.totals import CurrencyTotalRead, TotalsRead


class ClosingStampRead(BaseModel):
    """The seal as it travels with the ticket: everything but the signature image, which
    only the closing summary carries (it can weigh hundreds of KB per closed operation)."""

    signed_by: str
    signed_role: str
    signed_at: datetime
    totals: TotalsRead
    files_count: int
    folder: str
    has_signature: bool = True

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_record(cls, record: Any) -> dict[str, Any]:
        return {
            "signed_by": record.signed_by,
            "signed_role": record.signed_role,
            "signed_at": record.signed_at,
            "totals": record.totals,
            "files_count": record.files_count,
            "folder": record.folder,
            "has_signature": True,
        }


class StampSignatureRead(BaseModel):
    step_code: str
    step_name: str
    role_name: str
    user_name: str
    signed_at: datetime
    signature_url: str


class ClosingStampFullRead(ClosingStampRead):
    signature_url: str
    signatures: list[StampSignatureRead] = []


class RoleProgressRead(BaseModel):
    role_id: UUID | None = None
    role_name: str
    done: int
    total: int
    pending: list[str]

    model_config = ConfigDict(from_attributes=True)


class ClosingExpenseRead(BaseModel):
    ticket_id: UUID
    code: str
    concept: str
    amount: float
    currency: str
    amount_base: float
    status: str
    counts: bool
    has_support: bool


class ClosingMoneyRead(BaseModel):
    base: float
    by_currency: list[CurrencyTotalRead] = []


class ClosingSupportRead(BaseModel):
    ticket_id: UUID
    ticket_code: str
    file_id: UUID
    document_type: str
    name: str
    mime: str
    size: int
    filename: str
    folder: str
    uploaded_by: str

    model_config = ConfigDict(from_attributes=True)


class SupportTypeCountRead(BaseModel):
    document_type: str
    count: int


class ClosingBlockerRead(BaseModel):
    kind: str
    ticket_id: UUID
    ticket_code: str
    detail: str | None = None
    step_code: str | None = None
    role_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SignerRead(BaseModel):
    """One signature the closing demands, and whether the current user can give it."""

    step_code: str
    step_name: str
    role_code: str | None = None
    role_name: str
    required: bool = True
    signed: bool
    signed_by_name: str | None = None
    signed_at: datetime | None = None
    can_sign: bool
    has_signature: bool


class ClosingSummaryRead(BaseModel):
    ticket_id: UUID
    ticket_code: str
    closed: bool
    progress: list[RoleProgressRead]
    expenses: list[ClosingExpenseRead]
    totals: TotalsRead
    confirmed: ClosingMoneyRead
    in_progress: ClosingMoneyRead
    supports: list[ClosingSupportRead]
    supports_by_type: list[SupportTypeCountRead]
    blockers: list[ClosingBlockerRead]
    ready: bool
    signers: list[SignerRead]
    stamp: ClosingStampFullRead | None = None
