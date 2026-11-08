"""Extracción defensiva del JSON del modelo y validación de CADA valor en el servidor.

La salida del modelo es dato no confiable (el texto del comprobante puede intentar inyectar
instrucciones). Lo que protege es esta validación: lo inválido se descarta, no se adivina.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable

from .base import ReadHints
from .types import Confidence, ReadField, ReceiptReading

MAX_AMOUNT = Decimal("10000000")
MAX_SUPPLIER = 120
MAX_AGE_DAYS = 5 * 366
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT_RE = re.compile(r"^\d{1,12}(\.\d{1,6})?$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_FENCE = "```"
# A model answer can be steered by text printed on the receipt: bound the work per answer.
_MAX_OBJECT_STARTS = 64


def _fenced_blocks(text: str) -> list[str]:
    """Contents of the ``` fenced blocks, in order. Plain splitting, linear: a regex with
    `\\s*(.*?)` after the fence is quadratic on a long run of whitespace."""
    parts = text.split(_FENCE)
    blocks = []
    for index in range(1, len(parts) - 1, 2):
        block = parts[index]
        if block.startswith(("json", "JSON")):
            block = block[4:]
        blocks.append(block.strip())
    return blocks


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Primer objeto JSON del texto, tolerando code fences y prosa alrededor."""
    candidates = [text.strip()]
    candidates += _fenced_blocks(text)
    decoder = json.JSONDecoder()
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (ValueError, RecursionError):
            obj = None
            idx = cand.find("{")
            starts = 0
            while idx != -1 and obj is None and starts < _MAX_OBJECT_STARTS:
                starts += 1
                try:
                    obj, _ = decoder.raw_decode(cand, idx)
                except (ValueError, RecursionError):
                    obj = None
                    idx = cand.find("{", idx + 1)
        if isinstance(obj, dict):
            return obj
    return None


def clean_amount(raw: Any) -> str | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        if isinstance(raw, (int, float)):
            if isinstance(raw, float) and not math.isfinite(raw):
                return None
            value = Decimal(repr(raw))
        elif isinstance(raw, str):
            text = raw.strip()
            if not _AMOUNT_RE.match(text):
                return None
            value = Decimal(text)
        else:
            return None
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0:
        return None
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value <= 0 or value > MAX_AMOUNT:
        return None
    return format(value, "f")


def clean_currency(raw: Any, allowed: tuple[str, ...]) -> str | None:
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    allowed_up = {c.upper(): c for c in allowed}
    return allowed_up.get(code)


def clean_date(raw: Any, today: date) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not _DATE_RE.match(text):
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    if parsed > today or parsed < today - timedelta(days=MAX_AGE_DAYS):
        return None
    return parsed.isoformat()


def clean_supplier(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > MAX_SUPPLIER or _CTRL_RE.search(text):
        return None
    return text


def clean_concept(raw: Any, catalog: tuple[str, ...]) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text if text in catalog else None


def _confidence(conf_map: Any, name: str) -> Confidence:
    if isinstance(conf_map, dict) and conf_map.get(name) == "high":
        return "high"
    return "low"


def build_reading(
    payload: dict[str, Any],
    hints: ReadHints,
    provider: str,
    today: Callable[[], date] = date.today,
) -> ReceiptReading:
    """Valida el objeto del modelo. Claves desconocidas se ignoran; lo inválido pasa a None/low."""
    if payload.get("legible") is False:
        return ReceiptReading.unreadable(provider)
    day = today()
    conf = payload.get("confidence")
    specs = (
        ("amount", hints.amount_key, clean_amount(payload.get("amount"))),
        ("currency", hints.currency_key, clean_currency(payload.get("currency"), hints.currencies)),
        ("date", hints.accounting_date_key, clean_date(payload.get("date"), day)),
        ("supplier", hints.supplier_key, clean_supplier(payload.get("supplier"))),
        (
            "concept_code",
            hints.item_key,
            clean_concept(payload.get("concept_code"), hints.concept_codes),
        ),
    )
    fields: list[ReadField] = []
    for name, key, value in specs:
        level: Confidence = _confidence(conf, name) if value is not None else "low"
        fields.append(ReadField(key=key, value=value, confidence=level))
    if all(f.value is None for f in fields):
        return ReceiptReading.unreadable(provider)
    return ReceiptReading(status="extracted", fields=tuple(fields), provider=provider)
