"""JWT issue and verification on top of basekit's JWTService.

Differences from the base class, on purpose:
- configuration comes from Settings, not from os.environ;
- `iat` carries sub-second precision so revocation by `tokens_valid_after` is exact;
- access and refresh tokens are distinct (`token_type`) and not interchangeable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi_basekit.servicios import JWTService

from app.config.auth import TokenType
from app.config.settings import get_settings

CLOCK_LEEWAY_SECONDS = 5


@dataclass(frozen=True)
class TokenClaims:
    user_id: UUID
    issued_at: float
    expires_at: int
    token_type: TokenType


class TokenService(JWTService):
    def __init__(self) -> None:  # noqa: super().__init__ would read os.environ
        settings = get_settings()
        self.JWT_SECRET = settings.JWT_SECRET
        self.JWT_ALGORITHM = settings.JWT_ALGORITHM
        self.JWT_EXPIRE_SECONDS = settings.access_token_ttl_seconds
        self._refresh_ttl = settings.refresh_token_ttl_seconds

    def issue(self, user_id: UUID, token_type: TokenType) -> str:
        now = time.time()
        ttl = self.JWT_EXPIRE_SECONDS if token_type is TokenType.ACCESS else self._refresh_ttl
        return self.create_token(
            str(user_id),
            {"token_type": token_type.value, "iat": now, "exp": int(now) + ttl},
        )

    def read(self, token: str, expected: TokenType) -> TokenClaims | None:
        """Verified claims, or None for anything wrong (signature, expiry, type, shape)."""
        try:
            payload = jwt.decode(
                token,
                self.JWT_SECRET,
                algorithms=[self.JWT_ALGORITHM],
                options={"require": ["exp", "iat", "sub"]},
                leeway=CLOCK_LEEWAY_SECONDS,
            )
            if payload.get("token_type") != expected.value:
                return None
            return TokenClaims(
                user_id=UUID(str(payload["sub"])),
                issued_at=float(payload["iat"]),
                expires_at=int(payload["exp"]),
                token_type=expected,
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            return None
