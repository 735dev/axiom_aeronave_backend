"""Integration tests exercise the application's own code against the throw-away database.

Unlike tests/e2e, these may import backend modules: they verify properties that no HTTP
call can show (append-only behaviour below the API, registry/route drift).
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://aero:aero@localhost:5440/aero_test"
)
os.environ.update(
    {
        "DATABASE_URL": TEST_DB_URL,
        "MIGRATION_DATABASE_URL": TEST_DB_URL,
        "JWT_SECRET": "test-secret-test-secret-test-secret-0123456789",
        "ENVIRONMENT": "test",
        "RUNNING_TESTS": "true",
        "BCRYPT_ROUNDS": "4",
        "SEED_USER_PASSWORD": "",
    }
)

import asyncio  # noqa: E402

import asyncpg  # noqa: E402
import pytest  # noqa: E402


async def _reset() -> None:
    conn = await asyncpg.connect(TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def schema():
    asyncio.run(_reset())
    for args in (["-m", "alembic", "upgrade", "head"], ["-m", "app.scripts.init"]):
        done = subprocess.run(
            [sys.executable, *args], cwd=ROOT, env=os.environ, capture_output=True, text=True
        )
        if done.returncode:
            raise RuntimeError(done.stdout + done.stderr)
