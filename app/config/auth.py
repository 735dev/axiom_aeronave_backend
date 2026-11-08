"""Authentication constants."""

from __future__ import annotations

from enum import StrEnum

PASSWORD_MIN_LENGTH = 10
TEMPORARY_PASSWORD_BYTES = 12
FULL_NAME_MAX_LENGTH = 150
AVATAR_MAX_CHARS = 400_000

# Cost-equalisation hash: verified when the email is unknown so that the response
# takes the same time as a wrong password for an existing account.
DECOY_PASSWORD = "decoy-password-never-valid"


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


BEARER_SCHEME = "bearer"
