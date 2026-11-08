"""Password hashing (bcrypt).

bcrypt only reads the first 72 bytes and recent releases raise on longer input,
so longer passwords are rejected up front instead of being silently truncated.
"""

from __future__ import annotations

import bcrypt

from app.config.settings import get_settings

MAX_PASSWORD_BYTES = 72


def password_too_long(plain: str) -> bool:
    return len(plain.encode("utf-8")) > MAX_PASSWORD_BYTES


def hash_password(plain: str) -> str:
    if password_too_long(plain):
        raise ValueError("password exceeds 72 bytes")
    rounds = get_settings().BCRYPT_ROUNDS
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    if password_too_long(plain):
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False
