"""`python -m app.scripts.apply_aircraft_catalogs` upgrades an EXISTING database.

A scratch database is built with the project's own migration and seed, taken back to the
state before the change (brand/model as read-only text, no brand/model catalogs, fleet without
model) with the owner connection, and the script runs as a real process, twice. The shared
throw-away database is not touched.
"""

import os
import subprocess
import sys

import asyncpg
import pytest

from tests.integration.conftest import ROOT, TEST_DB_URL

SCRATCH = "aero_aircraft_upgrade_it"
ADMIN_DSN = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0]
SCRATCH_DSN = f"{ADMIN_DSN}/{SCRATCH}"
SCRATCH_URL = f"{TEST_DB_URL.rsplit('/', 1)[0]}/{SCRATCH}"


def run(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": SCRATCH_URL, "MIGRATION_DATABASE_URL": SCRATCH_URL}
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True
    )


async def snapshot(conn) -> dict:
    fields = await conn.fetch(
        "SELECT f.key, f.type, f.catalog, f.role, f.depends_on, f.read_only, f.sort_order "
        "FROM ticket_type_field f JOIN ticket_type t ON t.id = f.type_id "
        "WHERE t.code = 'operation' ORDER BY f.sort_order"
    )
    items = await conn.fetch(
        "SELECT catalog, code, extra FROM catalog_item "
        "WHERE catalog IN ('aircraft_brands', 'aircraft_models', 'aircrafts') "
        "ORDER BY catalog, code"
    )
    return {"fields": [tuple(r) for r in fields], "items": [tuple(r) for r in items]}


async def revert_to_before_the_change(conn) -> None:
    await conn.execute(
        "DELETE FROM catalog_item WHERE catalog IN ('aircraft_brands','aircraft_models')"
    )
    await conn.execute("UPDATE catalog_item SET extra = NULL WHERE catalog = 'aircrafts'")
    await conn.execute(
        "UPDATE ticket_type_field SET type = 'text', catalog = NULL, role = NULL, "
        "depends_on = NULL, read_only = true WHERE key IN ('aircraft_brand', 'aircraft_model') "
        "AND type_id = (SELECT id FROM ticket_type WHERE code = 'operation')"
    )
    # The old order: brand and model right after the control number, the aircraft far below.
    await conn.execute(
        "UPDATE ticket_type_field SET sort_order = CASE key WHEN 'aircraft_brand' THEN 3 "
        "WHEN 'aircraft_model' THEN 4 WHEN 'aircraft' THEN 99 ELSE sort_order + 10 END "
        "WHERE type_id = (SELECT id FROM ticket_type WHERE code = 'operation') "
        "AND key NOT IN ('date', 'control_number')"
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


def keys(state: dict) -> list[str]:
    return [f[0] for f in state["fields"]]


async def test_a_fresh_database_already_has_the_dropdowns(scratch):
    state = await snapshot(scratch)
    assert keys(state)[:5] == [
        "date",
        "control_number",
        "aircraft",
        "aircraft_brand",
        "aircraft_model",
    ]
    by_key = {f[0]: f for f in state["fields"]}
    assert by_key["aircraft_model"][1:6] == (
        "select",
        "aircraft_models",
        "aircraft_model",
        "aircraft_brand",
        False,
    )
    assert ("aircrafts", "YV2323", "king_air_200") in state["items"]


async def test_the_script_upgrades_an_old_database_and_is_idempotent(scratch):
    await revert_to_before_the_change(scratch)
    old = await snapshot(scratch)
    assert not [i for i in old["items"] if i[0] != "aircrafts"]
    assert {f[0]: f[1] for f in old["fields"]}["aircraft_brand"] == "text"

    first = run("-m", "app.scripts.apply_aircraft_catalogs")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "already applied" not in first.stdout
    upgraded = await snapshot(scratch)
    assert keys(upgraded)[:5] == [
        "date",
        "control_number",
        "aircraft",
        "aircraft_brand",
        "aircraft_model",
    ]
    by_key = {f[0]: f for f in upgraded["fields"]}
    assert by_key["aircraft_brand"][1:6] == (
        "select",
        "aircraft_brands",
        "aircraft_brand",
        None,
        False,
    )
    assert by_key["aircraft_model"][4] == "aircraft_brand"
    assert ("aircraft_brands", "beechcraft", None) in upgraded["items"]
    assert ("aircraft_models", "cheyenne", "piper") in upgraded["items"]
    assert ("aircrafts", "YV2528", "cheyenne") in upgraded["items"]
    assert len(keys(upgraded)) == len(keys(old))

    second = run("-m", "app.scripts.apply_aircraft_catalogs")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "already applied" in second.stdout
    assert await snapshot(scratch) == upgraded


async def test_a_model_an_administrator_already_linked_is_respected(scratch):
    await revert_to_before_the_change(scratch)
    await scratch.execute("UPDATE catalog_item SET extra = 'cheyenne' WHERE code = 'YV2323'")
    done = run("-m", "app.scripts.apply_aircraft_catalogs")
    assert done.returncode == 0, done.stdout + done.stderr
    state = await snapshot(scratch)
    assert ("aircrafts", "YV2323", "cheyenne") in state["items"]
    assert ("aircrafts", "YV2528", "cheyenne") in state["items"]
