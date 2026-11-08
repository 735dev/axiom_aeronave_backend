"""Constants of the attachments module. Codes in `AttachmentCode` are part of the API contract."""

from __future__ import annotations

from enum import StrEnum

ALLOWED_EXTENSIONS = frozenset({"pdf", "doc", "docx", "png", "jpg", "jpeg"})
MAX_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_TICKET = 10
#: Receipts are only read when they are images the reader accepts.
READABLE_KINDS = frozenset({"png", "jpeg"})
LINK_TTL_SECONDS = 5 * 60

DISPLAY_NAME_MAX = 150
DISPLAY_STEM_MAX = 100
RAW_NAME_MAX = 1024
FIELD_KEY_MAX = 60
PROVIDER_BLACKBOX = "blackbox"
FOLDER_PREFIX = "Maestro - "

#: Detected content kind -> canonical MIME type stored with the attachment.
MIME_BY_KIND = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
#: Extension -> the content kinds that may legitimately carry it.
KINDS_BY_EXTENSION = {
    "pdf": ("pdf",),
    "png": ("png",),
    "jpg": ("jpeg",),
    "jpeg": ("jpeg",),
    "doc": ("doc",),
    "docx": ("docx",),
}
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-"
JPEG_MAGIC = b"\xff\xd8\xff"
ZIP_MAGICS = (b"PK\x03\x04",)
#: A .docx is a zip: bound what it may expand to and refuse the classic bomb ratios.
DOCX_MAX_ENTRIES = 5000
DOCX_MAX_UNCOMPRESSED = 200 * 1024 * 1024
DOCX_MAX_RATIO = 200
DOCX_REQUIRED_ENTRY = "[Content_Types].xml"
DOCX_REQUIRED_PREFIX = "word/"
#: Characters that never belong in a stored file name (controls and bidi overrides).
BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏")

#: Server-side reading of receipts.
EXTRACT_FIELD_CAP = 20
READER_PROVIDERS = frozenset({"fake", "anthropic", "off"})


class AttachmentCode(StrEnum):
    """`codigo` of the error body, as the contract defines for 400/413/422."""

    EXTENSION = "extension"
    EMPTY = "vacio"
    SIZE = "tamano"
    IDEMPOTENCY = "idempotencia"
    CONTENT = "tipo"
    LIMIT = "limite"
    FIELD = "campo"
    STORAGE = "almacenamiento"


#: Error `code`s of AttachmentError that surface a `codigo` in the body.
CODIGO_CODES = frozenset(c.value for c in AttachmentCode)

#: The upload route, which the generic idempotency middleware must not buffer or fingerprint
#: (a multipart body changes boundary on every retry; the service compares the content hash).
ATTACHMENT_UPLOAD_SEGMENT = "/attachments/"
