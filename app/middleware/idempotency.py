"""Idempotency-Key for the mutating ticket endpoints.

A client that retries (flaky mobile network, double click) sends the same key and gets the
original answer instead of a second ticket. Rules:

- the key is scoped to the caller and the path, so another user (or another resource)
  can never receive someone else's stored response;
- the same key with a different body is a client bug: 422 `codigo: idempotencia`;
- only successful answers are remembered. A rejection is not: the client fixes the input
  and sends it again with the same key;
- a request still running under that key answers 409 so the retry does not run twice.

Sits inside the access middleware: by the time it runs the caller is authenticated, and the
principal is already in the scope. Needs no state of its own; the cache is a table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config.attachments import ATTACHMENT_UPLOAD_SEGMENT
from app.config.settings import get_settings
from app.config.tickets import (
    IDEMPOTENCY_CODE,
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_KEY_PATTERN,
    IDEMPOTENCY_MAX_BODY_BYTES,
    IDEMPOTENT_METHODS,
    IDEMPOTENT_PATH_PREFIXES,
)
from app.repositories.idempotency import IdempotencyRepository
from app.utils.errors import error_body

logger = logging.getLogger(__name__)

REPLAY_HEADER = b"idempotent-replay"

#: `_read_body` result when the client hung up before sending the whole body.
CLIENT_GONE = object()


def _problem(status_code: int, detail: str) -> JSONResponse:
    body = error_body(status_code, detail)
    body["codigo"] = IDEMPOTENCY_CODE
    return JSONResponse(status_code=status_code, content=body)


def canonical(body: bytes) -> bytes:
    """Key order and whitespace must not make two equal bodies look different."""
    try:
        return json.dumps(
            json.loads(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    except (ValueError, RecursionError):
        # RecursionError: a body nested thousands of levels deep is not a 500, only not JSON
        # worth normalising.
        return body


def fingerprint(method: str, path: str, body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(method.encode())
    digest.update(b"\0")
    digest.update(path.encode())
    digest.update(b"\0")
    digest.update(canonical(body))
    return digest.hexdigest()


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        settings = get_settings()
        self.prefix = settings.API_V1_STR
        self.purge_interval = settings.IDEMPOTENCY_PURGE_INTERVAL_SECONDS
        self.repository = IdempotencyRepository()
        self._last_purge = 0.0

    def _applies(self, scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] not in IDEMPOTENT_METHODS:
            return False
        path = scope["path"]
        # An upload is multipart (a new boundary on every retry) and up to 20 MB: the
        # attachments service keys it by Idempotency-Key and the content hash instead.
        if scope["method"] == "POST" and path.endswith(ATTACHMENT_UPLOAD_SEGMENT):
            return False
        if not path.startswith(self.prefix):
            return False
        return path[len(self.prefix) :].startswith(IDEMPOTENT_PATH_PREFIXES)

    @staticmethod
    def _header(scope: Scope) -> str | None:
        for name, value in scope["headers"]:
            if name.decode("latin-1").lower() == IDEMPOTENCY_HEADER:
                return value.decode("latin-1").strip()
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        key = self._header(scope) if self._applies(scope) else None
        principal = scope.get("state", {}).get("principal")
        if key is None or principal is None:
            await self.app(scope, receive, send)
            return
        if not IDEMPOTENCY_KEY_PATTERN.match(key):
            await _problem(422, "La clave de idempotencia no es válida.")(scope, receive, send)
            return

        body = await self._read_body(receive)
        if body is CLIENT_GONE:
            return
        if body is None:
            await _problem(422, "El cuerpo es demasiado grande.")(scope, receive, send)
            return

        await self._purge_if_due()
        actor, path = str(principal.user_id), scope["path"]
        body_print = fingerprint(scope["method"], path, body)
        stored = await self.repository.claim(actor, path, key, body_print)
        if stored is not None:
            await self._answer_existing(stored, body_print)(scope, receive, send)
            return

        await self._run_and_store(scope, body, send, actor, path, key)

    async def _purge_if_due(self) -> None:
        """Drop expired records now and then; any replica may do it, the delete is idempotent."""
        now = time.monotonic()
        if now - self._last_purge < self.purge_interval:
            return
        self._last_purge = now
        try:
            await self.repository.purge_expired()
        except Exception:
            logger.exception("idempotency_purge_failed")

    @staticmethod
    async def _read_body(receive: Receive) -> bytes | None | object:
        """The body, None when it is over the limit, CLIENT_GONE when the peer hung up.

        After a disconnect the server's `receive()` answers `http.disconnect` immediately
        and forever: looping on it without yielding starves the event loop (100% CPU, no
        request served), so a disconnect must end the read.
        """
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return CLIENT_GONE
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > IDEMPOTENCY_MAX_BODY_BYTES:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    @staticmethod
    def _answer_existing(stored, body_print: str):
        if stored.fingerprint != body_print:
            return _problem(422, "Esa clave de idempotencia ya se usó con otro contenido.")
        if stored.state != "done" or stored.body is None:
            return _problem(409, "Esa operación sigue en curso. Espera un momento y reintenta.")

        class Replay:
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                await send(
                    {
                        "type": "http.response.start",
                        "status": stored.status_code,
                        "headers": [
                            (b"content-type", (stored.content_type or "application/json").encode()),
                            (b"content-length", str(len(stored.body)).encode()),
                            (REPLAY_HEADER, b"true"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": stored.body})

        return Replay()

    async def _run_and_store(
        self, scope: Scope, body: bytes, send: Send, actor: str, path: str, key: str
    ) -> None:
        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        status_code = 500
        content_type: str | None = None
        chunks: list[bytes] = []

        async def capture(message: Message) -> None:
            nonlocal status_code, content_type
            if message["type"] == "http.response.start":
                status_code = message["status"]
                for name, value in message.get("headers", []):
                    if name.lower() == b"content-type":
                        content_type = value.decode("latin-1")
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, capture)
        except BaseException:
            await self._release(actor, path, key)
            raise
        if 200 <= status_code < 300:
            await self._complete(actor, path, key, status_code, content_type, b"".join(chunks))
        else:
            await self._release(actor, path, key)

    async def _complete(self, actor, path, key, status_code, content_type, body) -> None:
        try:
            await self.repository.complete(actor, path, key, status_code, content_type, body)
        except Exception:
            # The write already happened and was answered; a retry would find a pending
            # claim and wait it out. Log loudly: the cache is degraded, not the data.
            logger.exception("idempotency_store_failed path=%s", path)

    async def _release(self, actor, path, key) -> None:
        try:
            await self.repository.release(actor, path, key)
        except Exception:
            logger.exception("idempotency_release_failed path=%s", path)
