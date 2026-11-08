"""Client address of a request."""

from __future__ import annotations

from starlette.requests import HTTPConnection

from app.config.settings import get_settings

UNKNOWN_IP = "unknown"


def client_ip(connection: HTTPConnection) -> str:
    """Peer address; with TRUST_FORWARDED_FOR, the last X-Forwarded-For hop.

    The last hop is the one appended by our own proxy; anything before it is client
    supplied and can be forged, so it is never used.
    """
    if get_settings().TRUST_FORWARDED_FOR:
        forwarded = connection.headers.get("x-forwarded-for")
        if forwarded:
            last = forwarded.split(",")[-1].strip()
            if last:
                return last
    client = connection.client
    return client.host if client else UNKNOWN_IP
