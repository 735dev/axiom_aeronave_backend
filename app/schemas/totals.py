from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CurrencyTotalRead(BaseModel):
    currency: str
    currency_name: str | None = None
    amount: float
    amount_base: float
    count: int

    model_config = ConfigDict(from_attributes=True)


class ConceptTotalRead(BaseModel):
    key: str
    label: str
    amount_base: float
    count: int

    model_config = ConfigDict(from_attributes=True)


class TotalsRead(BaseModel):
    """Expense totals: confirmed, in progress and excluded kept apart on purpose."""

    base_currency: str
    committed_base: float
    pending_base: float
    excluded_base: float
    by_currency: list[CurrencyTotalRead] = []
    by_concept: list[ConceptTotalRead] = []

    model_config = ConfigDict(from_attributes=True)
