"""Lector determinista para tests y desarrollo (sin red). Hash de bytes -> misma lectura."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Callable

from .base import ReadHints, validate_image
from .types import ReadField, ReceiptReading

PROVIDER = "fake"
_SUPPLIERS = ("Estación de Servicio El Sol", "PDV Aeropuerto", "Hotel Plaza", "Taxi Aeropuerto")


class FakeReceiptReader:
    PROVIDER = PROVIDER

    def __init__(self, today: Callable[[], date] = date.today):
        self._today = today

    async def read(self, image: bytes, mime: str, hints: ReadHints) -> ReceiptReading:
        validate_image(image, mime)
        h = hashlib.sha256(image).digest()
        if h[0] % 7 == 0:  # ~1 de cada 7 ilegible, como el mock del front
            return ReceiptReading.unreadable(PROVIDER)
        amount = (int.from_bytes(h[1:4], "big") % 99000 + 500) / 100
        currency = hints.currencies[h[4] % len(hints.currencies)] if hints.currencies else None
        concept = (
            hints.concept_codes[h[5] % len(hints.concept_codes)] if hints.concept_codes else None
        )
        when = self._today() - timedelta(days=h[6] % 30)

        def conf(i: int) -> str:
            return "low" if h[7 + i] % 4 == 0 else "high"

        fields = (
            ReadField(hints.amount_key, f"{amount:.2f}", conf(0)),
            ReadField(hints.currency_key, currency, conf(1) if currency else "low"),
            ReadField(hints.accounting_date_key, when.isoformat(), conf(2)),
            ReadField(hints.supplier_key, _SUPPLIERS[h[8] % len(_SUPPLIERS)], conf(3)),
            ReadField(hints.item_key, concept, conf(4) if concept else "low"),
        )
        return ReceiptReading("extracted", fields, PROVIDER)
