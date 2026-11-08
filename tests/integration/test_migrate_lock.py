"""app.scripts.migrate: several replicas booting together serialise on an advisory lock."""

import asyncio
import os
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from app.scripts.migrate import LOCK_KEY
from tests.integration.conftest import ROOT, TEST_DB_URL


def _with_db(url: str, name: str) -> str:
    parts = urlparse(url)
    return urlunparse(parts._replace(path=f"/{name}"))


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture
async def scratch_db():
    """An empty database of its own: the suite's database is already migrated."""
    name = f"aero_mig_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_plain(_with_db(TEST_DB_URL, "postgres")))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = _with_db(TEST_DB_URL, name)
    yield url
    admin = await asyncpg.connect(_plain(_with_db(TEST_DB_URL, "postgres")))
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.close()


def start(url: str, *args: str, **env: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "app.scripts.migrate", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url, "MIGRATION_DATABASE_URL": url, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


async def finish(process: subprocess.Popen, timeout: float = 120) -> tuple[int, str]:
    loop = asyncio.get_running_loop()
    out, _ = await loop.run_in_executor(None, lambda: process.communicate(timeout=timeout))
    return process.returncode, out


async def head_and_users(url: str) -> tuple[str | None, int]:
    conn = await asyncpg.connect(_plain(url))
    try:
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        users = await conn.fetchval("SELECT count(*) FROM app_user")
        return version, users
    finally:
        await conn.close()


async def test_a_waiting_replica_does_not_touch_the_database_until_the_lock_is_free(scratch_db):
    holder = await asyncpg.connect(_plain(scratch_db))
    try:
        assert await holder.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY)
        process = start(scratch_db)
        await asyncio.sleep(3)
        assert process.poll() is None, "it must be waiting for the lock"
        tables = await holder.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )
        assert tables == 0, "nothing may run while another replica holds the lock"
        await holder.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    finally:
        await holder.close()
    code, out = await finish(process)
    assert code == 0, out
    assert "waited" in out
    version, users = await head_and_users(scratch_db)
    assert version and users == 6


async def test_two_replicas_started_together_both_succeed_and_seed_once(scratch_db):
    first, second = start(scratch_db), start(scratch_db)
    (code_a, out_a), (code_b, out_b) = await asyncio.gather(finish(first), finish(second))
    assert code_a == 0, out_a
    assert code_b == 0, out_b
    version, users = await head_and_users(scratch_db)
    assert version and users == 6
    # Exactly one of them ran the migrations that create tables; the other found them done.
    applied = [out for out in (out_a, out_b) if "Running upgrade" in out]
    assert len(applied) == 1, (out_a, out_b)


async def test_a_lock_that_never_frees_ends_in_a_clear_error_not_a_hang(scratch_db):
    holder = await asyncpg.connect(_plain(scratch_db))
    try:
        assert await holder.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY)
        started = time.monotonic()
        process = start(scratch_db, MIGRATION_LOCK_TIMEOUT_SECONDS="2")
        code, out = await finish(process, timeout=60)
        assert code != 0 and "migration lock" in out
        assert time.monotonic() - started < 30
    finally:
        await holder.close()


async def test_a_crashed_holder_frees_the_lock(scratch_db):
    holder = await asyncpg.connect(_plain(scratch_db))
    assert await holder.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY)
    holder.terminate()  # connection dropped without unlocking, like a killed replica
    code, out = await finish(start(scratch_db))
    assert code == 0, out


async def test_a_bad_configuration_fails_the_replica_loudly(scratch_db):
    code, out = await finish(start(scratch_db, ENVIRONMENT="production", JWT_SECRET="short"))
    assert code != 0
