"""Server-side validation of an uploaded file: name, extension, size and real content.

The client's MIME type and file name are claims, not facts. What decides is the extension
(allowlist) AND the first bytes of the content, which must agree. Pure functions: they
raise `AttachmentError` and touch neither the database nor the network.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.config.attachments import (
    ALLOWED_EXTENSIONS,
    BIDI_CONTROLS,
    DISPLAY_NAME_MAX,
    DISPLAY_STEM_MAX,
    DOCX_MAX_ENTRIES,
    DOCX_MAX_RATIO,
    DOCX_MAX_UNCOMPRESSED,
    DOCX_REQUIRED_ENTRY,
    DOCX_REQUIRED_PREFIX,
    JPEG_MAGIC,
    KINDS_BY_EXTENSION,
    MAX_BYTES,
    MIME_BY_KIND,
    OLE_MAGIC,
    PDF_MAGIC,
    PNG_MAGIC,
    RAW_NAME_MAX,
    ZIP_MAGICS,
    AttachmentCode,
)
from app.exceptions.attachments import AttachmentError

_NAME_CLEAN = re.compile(r"[^\w .()\-]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class UploadInfo:
    """A file that passed every check."""

    name: str
    extension: str
    kind: str
    mime: str
    size: int


def _strip_controls(text: str) -> str:
    return "".join(
        ch
        for ch in text
        if ch not in BIDI_CONTROLS and unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co"}
    )


def split_name(raw: str | None) -> tuple[str, str]:
    """(stem, extension) of what the client called the file: no directories, no controls,
    no traversal. The extension is only the LAST suffix (`a.pdf.exe` is an `.exe`)."""
    text = _strip_controls(unicodedata.normalize("NFC", raw or ""))
    base = PurePosixPath(text.replace("\\", "/")).name.strip()
    stem, dot, extension = base.rpartition(".")
    if not dot:
        return base, ""
    return stem, extension.strip().lower()


def display_name(stem: str, extension: str) -> str:
    clean = _SPACES.sub(" ", _NAME_CLEAN.sub("_", stem)).strip(" ._-")[:DISPLAY_STEM_MAX]
    clean = clean or "archivo"
    return f"{clean}.{extension}"[:DISPLAY_NAME_MAX]


def sniff_kind(content: bytes) -> str | None:
    """The kind of file the first bytes say it is, or None if it is none we accept."""
    if content.startswith(PDF_MAGIC):
        return "pdf"
    if content.startswith(PNG_MAGIC):
        return "png"
    if content.startswith(JPEG_MAGIC):
        return "jpeg"
    if content.startswith(OLE_MAGIC):
        return "doc"
    if any(content.startswith(magic) for magic in ZIP_MAGICS):
        return "docx" if _is_wordprocessing_zip(content) else None
    return None


def _is_wordprocessing_zip(content: bytes) -> bool:
    """An OOXML word document, checked from the zip directory only (nothing is expanded)."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > DOCX_MAX_ENTRIES:
                return False
            expanded = sum(e.file_size for e in entries)
            if expanded > DOCX_MAX_UNCOMPRESSED:
                return False
            if expanded > len(content) * DOCX_MAX_RATIO:
                return False
            names = {e.filename for e in entries}
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        return False
    return DOCX_REQUIRED_ENTRY in names and any(n.startswith(DOCX_REQUIRED_PREFIX) for n in names)


def inspect_upload(raw_name: str | None, content: bytes) -> UploadInfo:
    """Validate a file and describe it. Order mirrors Blackbox: extension, empty, size."""
    if raw_name is not None and len(raw_name) > RAW_NAME_MAX:
        raise AttachmentError(AttachmentCode.EXTENSION, "El nombre del archivo es demasiado largo.")
    stem, extension = split_name(raw_name)
    if extension not in ALLOWED_EXTENSIONS:
        raise AttachmentError(
            AttachmentCode.EXTENSION,
            "Tipo de archivo no permitido. Usa PDF, Word (DOC/DOCX), PNG o JPG.",
        )
    if not content:
        raise AttachmentError(AttachmentCode.EMPTY, "El archivo está vacío.")
    if len(content) > MAX_BYTES:
        raise AttachmentError(AttachmentCode.SIZE, "El archivo supera el máximo de 20 MB.")
    kind = sniff_kind(content)
    if kind is None or kind not in KINDS_BY_EXTENSION[extension]:
        raise AttachmentError(
            AttachmentCode.CONTENT,
            "El contenido del archivo no corresponde a su extensión.",
        )
    return UploadInfo(
        name=display_name(stem, extension),
        extension=extension,
        kind=kind,
        mime=MIME_BY_KIND[kind],
        size=len(content),
    )
