"""`python -m app.scripts.apply_operation_form_fixes` upgrades an EXISTING database.

A scratch database is built with the project's own migration and seed, taken back to the
state before the change (passengers as a plain number, exchange rate without a role) and the script runs as a real
process, twice. The shared throw-away database is not touched.
"""

import os
import subprocess
import sys

import asyncpg
import pytest

from tests.integration.conftest import ROOT, TEST_DB_URL

SCRATCH = "aero_form_fixes_upgrade_it"
ADMIN_DSN = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0]
SCRATCH_DSN = f"{ADMIN_DSN}/{SCRATCH}"
SCRATCH_URL = f"{TEST_DB_URL.rsplit('/', 1)[0]}/{SCRATCH}"
FIELD_SQL = (
    "SELECT f.key, f.type, f.role, f.catalog, f.sort_order FROM ticket_type_field f "
    "JOIN ticket_type t ON t.id = f.type_id WHERE t.code = 'operation' "
    "AND f.key IN ('passengers', 'exchange_rate', 'captain', 'fbo_name', 'fuel_supplier') ORDER BY f.key"
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


async def revert(conn) -> None:
    where = "AND type_id = (SELECT id FROM ticket_type WHERE code = 'operation')"
    await conn.execute(
        f"UPDATE ticket_type_field SET type = 'number' WHERE key = 'passengers' {where}"
    )
    await conn.execute(
        f"UPDATE ticket_type_field SET role = NULL WHERE key = 'exchange_rate' {where}"
    )
    await conn.execute(
        "UPDATE ticket_type_field SET type = 'text', catalog = NULL "
        f"WHERE key IN ('captain', 'fbo_name', 'fuel_supplier') {where}"
    )


def shape(rows) -> dict:
    return {r["key"]: (r["type"], r["role"], r["catalog"]) for r in rows}


NEW = {
    "captain": ("select", None, "users:pilot"),
    "exchange_rate": ("number", "exchange_rate", None),
    "fbo_name": ("select", None, "fbos"),
    "fuel_supplier": ("select", None, "fuel_suppliers"),
    "passengers": ("integer", None, None),
}
OLD = {
    "captain": ("text", None, None),
    "exchange_rate": ("number", None, None),
    "fbo_name": ("text", None, None),
    "fuel_supplier": ("text", None, None),
    "passengers": ("number", None, None),
}


async def test_a_fresh_database_already_has_everything(scratch):
    assert shape(await scratch.fetch(FIELD_SQL)) == NEW


async def test_the_script_upgrades_an_old_database_and_is_idempotent(scratch):
    await revert(scratch)
    before = await scratch.fetch(FIELD_SQL)
    assert shape(before) == OLD

    first = run("-m", "app.scripts.apply_operation_form_fixes")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "already applied" not in first.stdout
    after = await scratch.fetch(FIELD_SQL)
    assert shape(after) == NEW
    assert [r["sort_order"] for r in after] == [r["sort_order"] for r in before]

    second = run("-m", "app.scripts.apply_operation_form_fixes")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "already applied" in second.stdout
