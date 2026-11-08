"""Comparable forms of a passenger's name and document. Pure functions."""

from __future__ import annotations

import re
import unicodedata

from app.config.closing import MIN_DOCUMENT_QUERY

_DOCUMENT_NOISE = re.compile(r"[.\s\-_/]")
_COMBINING = re.compile(r"[̀-ͯ]")
_WHITESPACE = re.compile(r"\s+")


def fold_text(value: str) -> str:
    """Accent-free lowercase with single spaces: 'María  Pérez' and 'maria perez' match."""
    decomposed = unicodedata.normalize("NFD", value or "")
    return _WHITESPACE.sub(" ", _COMBINING.sub("", decomposed)).strip().lower()


def normalize_document(value: str) -> str:
    """Document without dots, spaces, dashes, underscores or slashes, in uppercase:
    'V-12.345.678' and 'V12345678' are the same document."""
    return _DOCUMENT_NOISE.sub("", value or "").upper()


def document_query(raw: str) -> str | None:
    """The normalized document to search, or None when the text cannot be one.

    A document needs a digit and a minimum length: otherwise 'ana' would also bring the
    passport 'ANA123' and a two-character query would match nearly everyone."""
    candidate = normalize_document(raw)
    if len(candidate) >= MIN_DOCUMENT_QUERY and any(ch.isdigit() for ch in candidate):
        return candidate
    return None


def clean_query(raw: str) -> str:
    """The text a search may use: control and format characters (NUL, bidi overrides...) are
    dropped. Postgres refuses a NUL byte, and a hidden character matches nothing visible."""
    return "".join(ch for ch in raw or "" if not unicodedata.category(ch).startswith("C"))
