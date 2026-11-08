"""Migrate and seed under a Postgres advisory lock: `python -m app.scripts.migrate [--no-seed]`.

Every replica's entrypoint runs this. Without a lock, two replicas booting together both run
`alembic upgrade head` on an empty database and one dies half way (or both fight over the
seed rows). Here the first one takes a session-level advisory lock, the others wait for it
and then find the database already at head (Alembic and the seed are both idempotent).

The lock is held by a dedicated connection with the owner role (MIGRATION_DATABASE_URL) for
as long as the child steps run; Postgres drops it by itself if this process dies, so a crashed
replica never leaves the others stuck. Waiting is bounded (MIGRATION_LOCK_TIMEOUT_SECONDS).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time

import asyncpg

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

#: Arbitrary constant shared by every replica ("aero" + "mig" in ASCII).
LOCK_KEY = 0x6165726F6D6967
POLL_SECONDS = 0.5
STEPS: dict[str, list[str]] = {
    "migrations": [sys.executable, "-m", "alembic", "upgrade", "head"],
    "seed": [sys.executable, "-m", "app.scripts.init"],
}


class MigrationLockTimeout(RuntimeError):
    pass


def plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def acquire(conn: asyncpg.Connection, timeout: float) -> float:
    """Wait for the lock; returns the seconds spent waiting. Polls instead of blocking so a
    stuck holder turns into a clear error rather than a replica that never starts."""
    started = time.monotonic()
    while not await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
        if time.monotonic() - started > timeout:
            raise MigrationLockTimeout(
                f"another process held the migration lock for more than {timeout:.0f}s"
            )
        await asyncio.sleep(POLL_SECONDS)
    return time.monotonic() - started


def run_step(name: str) -> None:
    logger.info("migrate: %s", name)
    done = subprocess.run(STEPS[name], env=os.environ, check=False)
    if done.returncode:
        raise SystemExit(f"migrate: step '{name}' failed with exit code {done.returncode}")


async def migrate(*, seed: bool = True) -> None:
    settings = get_settings()
    conn = await asyncpg.connect(plain_dsn(settings.migration_database_url), timeout=30)
    try:
        waited = await acquire(conn, settings.MIGRATION_LOCK_TIMEOUT_SECONDS)
        if waited >= POLL_SECONDS:
            logger.info("migrate: waited %.1fs for another replica", waited)
        # Blocking child processes: the event loop has nothing else to do while they run.
        run_step("migrations")
        if seed:
            run_step("seed")
    finally:
        # Closing the session releases the advisory lock.
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-seed", action="store_true", help="only run the migrations")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(migrate(seed=not args.no_seed))


if __name__ == "__main__":
    main()
