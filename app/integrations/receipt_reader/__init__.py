"""Lectura automática de comprobantes (§6.14): propone datos; la validación humana decide."""

from .anthropic_reader import AnthropicReceiptReader
from .base import ALLOWED_MIMES, MAX_IMAGE_BYTES, ReadHints, ReceiptReader
from .errors import ReceiptReaderError
from .factory import build_reader
from .fake_reader import FakeReceiptReader
from .service import ReaderRetry, ReceiptReadingService
from .types import ReadField, ReceiptReading

__all__ = [
    "ALLOWED_MIMES",
    "build_reader",
    "AnthropicReceiptReader",
    "FakeReceiptReader",
    "MAX_IMAGE_BYTES",
    "ReadField",
    "ReadHints",
    "ReaderRetry",
    "ReceiptReader",
    "ReceiptReaderError",
    "ReceiptReading",
    "ReceiptReadingService",
]
