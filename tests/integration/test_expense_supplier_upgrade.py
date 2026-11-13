"""`python -m app.scripts.apply_expense_supplier_catalog` upgrades an EXISTING database.

A scratch database is built with the project's own migration and seed, taken back to the
state before the change (Proveedor as free text) and the script runs as a real process,
twice. The shared throw-away database is not touched.
"""

import os
import subprocess
import sys

import asyncpg
import pytest

from tests.integration.conftest import ROOT, TEST_DB_URL

SCRATCH = "aero_expense_supplier_upgrade_it"
ADMIN_DSN = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0]
SCRATCH_DSN = f"{ADMIN_DSN}/{SCRATCH}"
SCRATCH_URL = f"{TEST_DB_URL.rsplit('/', 1)[0]}/{SCRATCH}"
FIELD_SQL = (
    "SELECT f.type, f.catalog, f.role, f.sort_order FROM ticket_type_field f "
    "JOIN ticket_type t ON t.id = f.type_id WHERE t.code = 'expense' AND f.key = 'supplier'"
)


def run(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": SCRATCH_URL, "MIGRATION_DATABASE_URL": SCRATCH_URL}
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True
    )


@pytest.fixture
async def scratch():
    admin = await asyncpg.connect(f"{ADMIN_DSN}/postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{SCRATCH}"')
        for args in (["-m", "alembic", "upgrade", "head"], ["-m", "app.scripts.init"]):
            done = run(*args)
            assert done.returncode == 0, done.stdout + done.stderr
        conn = await asyncpg.connect(SCRATCH_DSN)
        try:
            yield conn
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
        await admin.close()


async def revert_to_free_text(conn) -> None:
    await conn.execute(
        "UPDATE ticket_type_field SET type = 'text', catalog = NULL WHERE key = 'supplier' "
        "AND type_id = (SELECT id FROM ticket_type WHERE code = 'expense')"
    )


async def test_a_fresh_database_already_has_the_catalog_select(scratch):
    row = await scratch.fetchrow(FIELD_SQL)
    assert (row["type"], row["catalog"], row["role"]) == ("select", "suppliers", "supplier")


async def test_the_script_upgrades_an_old_database_and_is_idempotent(scratch):
    await revert_to_free_text(scratch)
    before = await scratch.fetchrow(FIELD_SQL)
    assert before["type"] == "text"

    first = run("-m", "app.scripts.apply_expense_supplier_catalog")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "already applied" not in first.stdout
    after = await scratch.fetchrow(FIELD_SQL)
    assert (after["type"], after["catalog"]) == ("select", "suppliers")
    assert after["sort_order"] == before["sort_order"]

    second = run("-m", "app.scripts.apply_expense_supplier_catalog")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "already applied" in second.stdout
