"""CSV writing for reconciliation. Excel opens a cell that starts with = + - @ (or a tab or
carriage return) as a formula, so user-written text is neutralized before it is written."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from app.config.closing import CSV_BOM, CSV_FORMULA_PREFIXES, CSV_SEPARATOR

CsvValue = str | int | float | Decimal | None


def neutralize_formula(text: str) -> str:
    return f"'{text}" if text.startswith(CSV_FORMULA_PREFIXES) else text


def cell(value: CsvValue) -> str:
    """Every cell is quoted, inner quotes doubled. A number is a legitimate negative amount
    and goes out with a decimal comma, without the formula guard."""
    if value is None:
        return '""'
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        text = f"{Decimal(str(value)):.2f}".replace(".", ",")
    else:
        text = neutralize_formula(str(value))
    return '"' + text.replace('"', '""') + '"'


def build_csv(headers: Sequence[str], rows: Iterable[Sequence[CsvValue]]) -> str:
    """UTF-8 text with BOM (Excel keeps the accents) and CRLF line ends."""
    lines = [CSV_SEPARATOR.join(cell(h) for h in headers)]
    lines.extend(CSV_SEPARATOR.join(cell(v) for v in row) for row in rows)
    return CSV_BOM + "\r\n".join(lines) + "\r\n"
