"""Validation of the signature image received as a data URL."""

from __future__ import annotations

import base64
import binascii
import re
import struct
from dataclasses import dataclass

from app.config.signature import (
    PNG_MAGIC,
    SIGNATURE_MAGIC_BY_MIME,
    SIGNATURE_MAX_BYTES,
    SIGNATURE_MIME_TYPES,
)

_DATA_URL = re.compile(r"^data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})$")
_PNG_IHDR_END = 24
_MAX_DIMENSION = 8000


class SignatureProblem(Exception):
    """`code` is one of: format, type, empty, size, content."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedSignature:
    mime: str
    size: int
    data_url: str


def _png_dimensions_ok(data: bytes) -> bool:
    if len(data) < _PNG_IHDR_END or data[12:16] != b"IHDR":
        return False
    width, height = struct.unpack(">II", data[16:24])
    return 0 < width <= _MAX_DIMENSION and 0 < height <= _MAX_DIMENSION


def parse_signature(raw: str) -> ParsedSignature:
    """Validate type, size and real content. The declared mime type is never trusted:
    the bytes must start with the matching magic number."""
    match = _DATA_URL.match(raw)
    if not match:
        raise SignatureProblem("format")
    mime, payload = match.group(1), match.group(2)
    if mime not in SIGNATURE_MIME_TYPES:
        raise SignatureProblem("type")
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureProblem("format") from None
    if not data:
        raise SignatureProblem("empty")
    if len(data) > SIGNATURE_MAX_BYTES:
        raise SignatureProblem("size")
    if not data.startswith(SIGNATURE_MAGIC_BY_MIME[mime]):
        raise SignatureProblem("content")
    if data.startswith(PNG_MAGIC) and not _png_dimensions_ok(data):
        raise SignatureProblem("content")
    normalized = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return ParsedSignature(mime=mime, size=len(data), data_url=normalized)
