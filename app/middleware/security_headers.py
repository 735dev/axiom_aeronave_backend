"""Security headers on every response (pure ASGI, so streaming is untouched)."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config.settings import get_settings

DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
HSTS = "max-age=31536000; includeSubDomains"

STATIC_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
    b"cross-origin-resource-policy": b"same-site",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.settings = get_settings()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"].startswith(DOCS_PREFIXES)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                extra = dict(STATIC_HEADERS)
                if not is_docs:
                    extra[b"content-security-policy"] = API_CSP.encode()
                    # API payloads carry personal data and tokens: never cache them.
                    extra[b"cache-control"] = b"no-store"
                if not self.settings.is_local or scope.get("scheme") == "https":
                    extra[b"strict-transport-security"] = HSTS.encode()
                for name, value in extra.items():
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, send_with_headers)
