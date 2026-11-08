"""HTTP-only helpers for the attachments, receipt reading and notification E2E files."""

from __future__ import annotations

import hashlib
import io
import itertools
import struct
import uuid
import zipfile

from tests.e2e.conftest import API, Api
from tests.e2e.tickets_support import make_expense, make_operation, ticket_url

_counter = itertools.count(1)


def _filler(n: int) -> bytes:
    return struct.pack(">Q", next(_counter)) + uuid.uuid4().bytes + b"\x00" * max(0, n - 24)


def png_bytes(size: int = 64) -> bytes:
    """A PNG-looking file with unique content (the signature is what the server checks)."""
    return b"\x89PNG\r\n\x1a\n" + _filler(size)


def jpeg_bytes(size: int = 64) -> bytes:
    return b"\xff\xd8\xff\xe0" + _filler(size)


def pdf_bytes(size: int = 64) -> bytes:
    return b"%PDF-1.4\n" + _filler(size)


def doc_bytes(size: int = 64) -> bytes:
    return bytes.fromhex("d0cf11e0a1b11ae1") + _filler(size)


def docx_bytes(extra_entries: int = 0, payload: bytes = b"<w:document/>") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", payload)
        for i in range(extra_entries):
            archive.writestr(f"word/extra{i}.xml", "x")
    return buffer.getvalue()


def zip_without_word() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hola")
    return buffer.getvalue()


def zip_bomb() -> bytes:
    """A real .docx structure whose entries expand ~100000x: a classic decompression bomb."""
    return docx_bytes(payload=b"0" * (30 * 1024 * 1024))


def _fake_reading(content: bytes) -> tuple[bool, list[bool]]:
    """What the deterministic fake reader does with these bytes (see FakeReceiptReader):
    (readable, low-confidence flag for each of the five fields)."""
    digest = hashlib.sha256(content).digest()
    return digest[0] % 7 != 0, [digest[7 + i] % 4 == 0 for i in range(5)]


def readable_png(low: bool | None = None) -> bytes:
    """A PNG the fake reader can read. `low=True` forces some low-confidence field,
    `low=False` makes every field high-confidence."""
    while True:
        candidate = png_bytes()
        readable, lows = _fake_reading(candidate)
        if not readable:
            continue
        if low is None or low == any(lows):
            return candidate


def unreadable_png() -> bytes:
    while True:
        candidate = png_bytes()
        if not _fake_reading(candidate)[0]:
            return candidate


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


MIME = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def mime_of(name: str) -> str:
    return MIME.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def files_url(ticket: dict | str, suffix: str = "") -> str:
    return ticket_url(ticket, f"attachments/{suffix}")


async def upload(
    api: Api,
    ticket: dict | str,
    name: str = "recibo.png",
    content: bytes | None = None,
    *,
    field_key: str | None = None,
    key: str | None = None,
    mime: str | None = None,
    headers: dict | None = None,
):
    """POST a multipart upload. With no `content`, a fresh file matching the extension."""
    if content is None:
        ext = name.rsplit(".", 1)[-1].lower()
        content = {
            "png": png_bytes,
            "jpg": jpeg_bytes,
            "jpeg": jpeg_bytes,
            "pdf": pdf_bytes,
            "doc": doc_bytes,
            "docx": docx_bytes,
        }.get(ext, pdf_bytes)()
    sent = dict(headers or {})
    if key is not None:
        sent["Idempotency-Key"] = key
    data = {"field_key": field_key} if field_key is not None else None
    return await api.post(
        files_url(ticket),
        files={"file": (name, content, mime or mime_of(name))},
        data=data,
        headers=sent,
    )


async def attach(api: Api, ticket: dict | str, name: str = "recibo.png", **kw) -> dict:
    r = await upload(api, ticket, name, **kw)
    assert r.status_code == 201, r.text
    return r.json()


async def files_of(api: Api, ticket: dict | str) -> list[dict]:
    r = await api.get(files_url(ticket))
    assert r.status_code == 200, r.text
    return r.json()


async def extract(api: Api, type_code: str, name: str, content: bytes, mime: str | None = None):
    return await api.post(
        f"{API}/attachments/extract/",
        files={"file": (name, content, mime or mime_of(name))},
        data={"type_code": type_code},
    )


async def new_expense(api: Api, operation: dict | None = None, **payload) -> tuple[dict, dict]:
    """(operation, expense) both created by `api`."""
    operation = operation or await make_operation(api)
    return operation, await make_expense(api, operation, **payload)


async def child_of(api: Api, parent: dict, type_code: str, **extra) -> dict:
    payload = {
        "expense": {"concept": "fuel", "amount": 100, "currency": "USD"},
        "inac_permit": {"scope": "domestic"},
        "technical_report": {"description": "falla"},
    }.get(type_code, {})
    body = {
        "type_code": type_code,
        "title": type_code,
        "parent_id": parent["id"],
        "payload": payload,
    }
    body.update(extra)
    r = await api.post(f"{API}/tickets/", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def notices(api: Api, **query) -> dict:
    params = "&".join(f"{k}={v}" for k, v in {"limit": 100, **query}.items())
    r = await api.get(f"{API}/notifications/?{params}")
    assert r.status_code == 200, r.text
    return r.json()


async def about(api: Api, ticket: dict | str, kind: str | None = None) -> list[dict]:
    """Notices this caller sees about a ticket."""
    ticket_id = ticket if isinstance(ticket, str) else ticket["id"]
    return [
        n
        for n in (await notices(api))["data"]
        if n["ticket_id"] == ticket_id and (kind is None or n["kind"] == kind)
    ]
