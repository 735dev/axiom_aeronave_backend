"""Contrato del lector de comprobantes y guardas de imagen compartidas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import ReceiptReaderError
from .types import ReceiptReading

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


@dataclass(frozen=True)
class ReadHints:
    """Lo que el servidor permite leer y cómo se llaman los campos del tipo de ticket.

    Los `*_key` son las claves de los campos del tipo de ticket mapeados por rol, de modo que
    la salida encaja directo con el formulario.
    """

    currencies: tuple[str, ...]
    concept_codes: tuple[str, ...]
    amount_key: str = "amount"
    currency_key: str = "currency"
    item_key: str = "item"
    accounting_date_key: str = "accounting_date"
    supplier_key: str = "supplier"


def validate_image(image: bytes, mime: str) -> str:
    """Devuelve el mime normalizado o levanta antes de cualquier llamada de red."""
    normalized = (mime or "").strip().lower()
    if normalized not in ALLOWED_MIMES:
        raise ReceiptReaderError("Tipo de imagen no soportado", code="imagen_tipo")
    if not image:
        raise ReceiptReaderError("Imagen vacía", code="imagen_vacia")
    if len(image) > MAX_IMAGE_BYTES:
        raise ReceiptReaderError("Imagen demasiado grande (máx. 5 MB)", code="imagen_tamano")
    return normalized


class ReceiptReader(Protocol):
    async def read(self, image: bytes, mime: str, hints: ReadHints) -> ReceiptReading: ...
