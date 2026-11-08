"""Refuses an oversized or length-less upload before any of its body is read.

A multipart parser spools the body to disk as it reads, so the limit has to be enforced on
the declared length, ahead of the parser: otherwise a 2 GB request fills the disk before the
service ever sees a byte. Only the upload routes are affected.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config.attachments import MAX_BYTES, AttachmentCode
from app.config.settings import get_settings
from app.utils.errors import error_body

#: Multipart framing (boundaries, headers, the small form fields) on top of the file.
OVERHEAD_BYTES = 64 * 1024
LIMIT = MAX_BYTES + OVERHEAD_BYTES


class UploadLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.prefix = get_settings().API_V1_STR

    def _is_upload(self, scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] != "POST":
            return False
        path = scope["path"]
        if not path.startswith(self.prefix):
            return False
        rest = path[len(self.prefix) :]
        if rest == "/attachments/extract/":
            return True
        parts = rest.strip("/").split("/")
        return len(parts) == 3 and parts[0] == "tickets" and parts[2] == "attachments"

    @staticmethod
    def _declared_length(scope: Scope) -> int | None:
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return -1
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_upload(scope):
            await self.app(scope, receive, send)
            return
        length = self._declared_length(scope)
        if length is None:
            body = error_body(411, "Indica el tamaño del archivo (Content-Length).")
            await JSONResponse(status_code=411, content=body)(scope, receive, send)
            return
        if length < 0 or length > LIMIT:
            body = error_body(
                413, "El archivo supera el máximo de 20 MB.", codigo=AttachmentCode.SIZE
            )
            await JSONResponse(status_code=413, content=body)(scope, receive, send)
            return
        await self.app(scope, receive, send)
