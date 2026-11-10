"""`python -m app.scripts.apply_tvoa_signatures` upgrades an EXISTING database.

A scratch database is built with the project's own migration and seed, taken back to the
state before the multi-signature change with the owner connection (no API can do that), and
the script runs as a real process, twice. The shared throw-away database is not touched.
"""

import os
import subprocess
import sys

import asyncpg
import pytest

from tests.integration.conftest import ROOT, TEST_DB_URL

SCRATCH = "aero_tvoa_upgrade_it"
ADMIN_DSN = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://").rsplit("/", 1)[0]
SCRATCH_DSN = f"{ADMIN_DSN}/{SCRATCH}"
SCRATCH_URL = f"{TEST_DB_URL.rsplit('/', 1)[0]}/{SCRATCH}"
SIGNING = ["sign_captain", "sign_maintenance", "sign_avsec", "sign_general"]


def run(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": SCRATCH_URL, "MIGRATION_DATABASE_URL": SCRATCH_URL}
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True
    )


async def snapshot(conn) -> dict:
    steps = lambda code: conn.fetch(  # noqa: E731
        "SELECT s.code, s.requires_signature, s.required, r.code AS role, s.responsible_area "
        "FROM ticket_type_step s JOIN ticket_type t ON t.id = s.type_id "
        "LEFT JOIN role r ON r.id = s.role_id WHERE t.code = $1 ORDER BY s.sort_order",
        code,
    )
    return {
        "operation": [tuple(r) for r in await steps("operation")],
        "report": [tuple(r) for r in await steps("technical_report")],
        "avsec": await conn.fetchval("SELECT count(*) FROM role WHERE code = 'avsec'"),
        "signers": sorted(
            r["code"]
            for r in await conn.fetch(
                "SELECT r.code FROM role r JOIN role_permission rp ON rp.role_id = r.id "
                "JOIN permission p ON p.id = rp.permission_id WHERE p.code = 'tickets.sign' AND r.code <> 'superuser'"
            )
        ),
    }


async def revert_to_before_the_change(conn) -> None:
    """The database as it was before: one signature of Operaciones, ASEC steps in Operaciones."""
    await conn.execute("DELETE FROM ticket_type_step WHERE code = ANY($1)", SIGNING)
    ops = await conn.fetchval("SELECT id FROM role WHERE code = 'operations'")
    type_id = await conn.fetchval("SELECT id FROM ticket_type WHERE code = 'operation'")
    await conn.execute(
        "INSERT INTO ticket_type_step (id, type_id, code, name, role_id, required, "
        "requires_signature, sort_order) VALUES (gen_random_uuid(), $1, 'signature', "
        "'Firma de cierre', $2, true, false, 5)",
        type_id,
        ops,
    )
    await conn.execute(
        "UPDATE ticket_type_step SET role_id = $1, responsible_area = 'Gerente ASEC' "
        "WHERE code IN ('movement', 'return')",
        ops,
    )
    await conn.execute("DELETE FROM app_user WHERE email = 'avsec@transvalor.com'")
    await conn.execute("DELETE FROM role WHERE code = 'avsec'")
    await conn.execute(
        "DELETE FROM role_permission WHERE permission_id IN "
        "(SELECT id FROM permission WHERE code = 'tickets.sign')"
    )
    await conn.execute("DELETE FROM permission WHERE code = 'tickets.sign'")


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


async def test_a_fresh_database_already_has_the_four_signatures(scratch):
    after = await snapshot(scratch)
    assert [s[0] for s in after["operation"] if s[1]] == SIGNING
    assert after["avsec"] == 1
    assert after["signers"] == ["avsec", "executive", "maintenance", "pilot"]


async def test_the_script_upgrades_an_old_database_and_is_idempotent(scratch):
    await revert_to_before_the_change(scratch)
    old = await snapshot(scratch)
    assert [s[0] for s in old["operation"]][-1] == "signature" and old["avsec"] == 0
    assert old["signers"] == []

    first = run("-m", "app.scripts.apply_tvoa_signatures")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "already applied" not in first.stdout
    upgraded = await snapshot(scratch)
    codes = [s[0] for s in upgraded["operation"]]
    assert "signature" not in codes
    assert [s[0] for s in upgraded["operation"] if s[1]] == SIGNING
    assert codes[:4] == ["planning", "requirements", "ground", "technical"]
    assert {s[0]: s[3] for s in upgraded["operation"] if s[1]} == {
        "sign_captain": "pilot",
        "sign_maintenance": "maintenance",
        "sign_avsec": "avsec",
        "sign_general": "executive",
    }
    handed = {s[0]: (s[3], s[4]) for s in upgraded["report"] if s[0] in ("movement", "return")}
    assert handed == {"movement": ("avsec", "Coord. AVSEC"), "return": ("avsec", "Coord. AVSEC")}
    assert upgraded["avsec"] == 1
    assert upgraded["signers"] == ["avsec", "executive", "maintenance", "pilot"]

    second = run("-m", "app.scripts.apply_tvoa_signatures")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "already applied" in second.stdout
    assert await snapshot(scratch) == upgraded


async def test_a_step_an_administrator_already_moved_is_respected(scratch):
    await revert_to_before_the_change(scratch)
    maintenance = await scratch.fetchval("SELECT id FROM role WHERE code = 'maintenance'")
    await scratch.execute(
        "UPDATE ticket_type_step SET role_id = $1 WHERE code = 'return'", maintenance
    )
    done = run("-m", "app.scripts.apply_tvoa_signatures")
    assert done.returncode == 0, done.stdout + done.stderr
    roles = {s[0]: s[3] for s in (await snapshot(scratch))["report"]}
    assert roles["movement"] == "avsec" and roles["return"] == "maintenance"
