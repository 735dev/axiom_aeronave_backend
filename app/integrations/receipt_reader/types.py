"""Tipos de salida de la lectura de comprobantes (inmutables)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["high", "low"]
Status = Literal["extracted", "unreadable"]


@dataclass(frozen=True)
class ReadField:
    """Un dato propuesto por la máquina. `value` None = no se pudo leer (siempre 'low')."""

    key: str
    value: str | None
    confidence: Confidence


@dataclass(frozen=True)
class ReceiptReading:
    status: Status
    fields: tuple[ReadField, ...]
    provider: str

    @classmethod
    def unreadable(cls, provider: str) -> "ReceiptReading":
        return cls(status="unreadable", fields=(), provider=provider)
