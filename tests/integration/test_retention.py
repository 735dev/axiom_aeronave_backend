"""Retention scripts run as real processes against the throw-away database.

Aged rows are planted with the owner connection (no API can back-date anything); the scripts
run exactly as cron would run them, and the result is read back from the database.
"""

import gzip
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.integration.conftest import ROOT, TEST_DB_URL

OWNER_DSN = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://")
RUNTIME_ROLE, MAINT_ROLE, PASSWORD = "aero_it_runtime", "aero_it_maint", "it-pass"


def as_role(role: str) -> str:
    """The test DB URL with another user (SQLAlchemy form)."""
    head, tail = TEST_DB_URL.split("//", 1)
    return f"{head}//{role}:{PASSWORD}@{tail.split('@', 1)[1]}"


def run_script(module: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


@pytest.fixture
async def owner():
    conn = await asyncpg.connect(OWNER_DSN)
    try:
        yield conn
    finally:
        await conn.close()


async def ensure_role(conn, role: str, grants: list[str]) -> None:
    if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", role):
        await conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{PASSWORD}'")
    await conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    await conn.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
    for grant in grants:
        await conn.execute(grant)


# --------------------------------------------------------------------------- notifications


async def plant_notification(conn, *, days_old: int, body: str) -> uuid.UUID:
    notice_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO notification (id, kind, title, body, created_at) "
        "VALUES ($1, 'operation_created', 't', $2, now() - make_interval(days => $3))",
        notice_id,
        body,
        days_old,
    )
    role_id = await conn.fetchval("SELECT id FROM role LIMIT 1")
    user_id = await conn.fetchval("SELECT id FROM app_user LIMIT 1")
    await conn.execute(
        "INSERT INTO notification_audience (id, notification_id, role_id) VALUES ($1, $2, $3)",
        uuid.uuid4(),
        notice_id,
        role_id,
    )
    await conn.execute(
        "INSERT INTO notification_read (notification_id, user_id) VALUES ($1, $2)",
        notice_id,
        user_id,
    )
    return notice_id


class TestNotificationPurge:
    async def test_purges_only_past_the_line_and_cascades(self, owner):
        tag = uuid.uuid4().hex
        old = await plant_notification(owner, days_old=200, body=f"viejo {tag}")
        fresh = await plant_notification(owner, days_old=5, body=f"nuevo {tag}")
        done = run_script("app.scripts.purge_notifications", NOTIFICATION_RETENTION_DAYS="30")
        assert done.returncode == 0, done.stderr
        assert await owner.fetchval("SELECT count(*) FROM notification WHERE id=$1", old) == 0
        assert await owner.fetchval("SELECT count(*) FROM notification WHERE id=$1", fresh) == 1
        for table in ("notification_audience", "notification_read"):
            assert (
                await owner.fetchval(f"SELECT count(*) FROM {table} WHERE notification_id=$1", old)
                == 0
            )
            assert (
                await owner.fetchval(
                    f"SELECT count(*) FROM {table} WHERE notification_id=$1", fresh
                )
                == 1
            )

    async def test_leaves_a_summary_event_without_content(self, owner):
        tag = uuid.uuid4().hex
        await plant_notification(owner, days_old=400, body=f"confidencial {tag}")
        run_script("app.scripts.purge_notifications", NOTIFICATION_RETENTION_DAYS="30")
        event = await owner.fetchrow(
            "SELECT * FROM audit_event WHERE entity='notification' AND action='delete' "
            "ORDER BY seq DESC LIMIT 1"
        )
        assert event["actor_id"] == "system" and "Purgó" in event["summary"]
        assert tag not in json.dumps(dict(event), default=str)

    async def test_dry_run_and_disabled_change_nothing(self, owner):
        old = await plant_notification(owner, days_old=999, body="viejo")
        dry = run_script(
            "app.scripts.purge_notifications", "--dry-run", NOTIFICATION_RETENTION_DAYS="30"
        )
        assert dry.returncode == 0 and "to purge" in dry.stdout
        off = run_script("app.scripts.purge_notifications", NOTIFICATION_RETENTION_DAYS="0")
        assert off.returncode == 0 and "purged: 0" in off.stdout
        assert await owner.fetchval("SELECT count(*) FROM notification WHERE id=$1", old) == 1

    async def test_the_runtime_role_is_enough(self, owner):
        await ensure_role(
            owner,
            RUNTIME_ROLE,
            [
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {RUNTIME_ROLE}",
                f"REVOKE ALL ON audit_event FROM {RUNTIME_ROLE}",
                f"GRANT SELECT, INSERT ON audit_event TO {RUNTIME_ROLE}",
            ],
        )
        old = await plant_notification(owner, days_old=300, body="viejo")
        done = run_script(
            "app.scripts.purge_notifications",
            NOTIFICATION_RETENTION_DAYS="30",
            DATABASE_URL=as_role(RUNTIME_ROLE),
        )
        assert done.returncode == 0, done.stderr
        assert await owner.fetchval("SELECT count(*) FROM notification WHERE id=$1", old) == 0


# --------------------------------------------------------------------------- audit archive


async def plant_audit(conn, *, days_old: int, summary: str) -> int:
    return await conn.fetchval(
        "INSERT INTO audit_event (id, at, actor_id, actor_name, actor_role, action, entity, "
        "entity_id, entity_label, summary) VALUES (gen_random_uuid(), now() - make_interval(days => $1), "
        "'a', 'Alguien', 'Rol', 'create', 'user', '1', 'x', $2) RETURNING seq",
        days_old,
        summary,
    )


def archive_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "MAINTENANCE_DATABASE_URL": TEST_DB_URL,
        "AUDIT_ARCHIVE_DIR": str(tmp_path),
        "AUDIT_RETENTION_DAYS": "100",
        **extra,
    }


class TestAuditArchive:
    async def test_archives_verifies_then_deletes_only_what_it_archived(self, owner, tmp_path):
        tag = uuid.uuid4().hex[:8]
        old = [
            await plant_audit(owner, days_old=200 + i, summary=f"viejo {tag} {i}") for i in range(3)
        ]
        young = await plant_audit(owner, days_old=10, summary=f"joven {tag}")
        done = run_script("app.scripts.audit_archive", **archive_env(tmp_path))
        assert done.returncode == 0, done.stdout + done.stderr

        files = sorted(tmp_path.glob("*.jsonl.gz"))
        assert len(files) == 1
        checksum = (tmp_path / (files[0].name + ".sha256")).read_text().split()[0]
        assert checksum == hashlib.sha256(files[0].read_bytes()).hexdigest()
        with gzip.open(files[0], "rt", encoding="utf-8") as packed:
            archived = [json.loads(line) for line in packed]
        mine = {a["seq"]: a for a in archived if tag in a["summary"]}
        assert set(mine) == set(old)  # nothing younger, nothing missing, lossless columns
        assert {"actor_name", "changes", "ip", "entity_label", "at"} <= set(archived[0])

        gone = await owner.fetchval("SELECT count(*) FROM audit_event WHERE seq = ANY($1)", old)
        kept = await owner.fetchval("SELECT count(*) FROM audit_event WHERE seq=$1", young)
        assert gone == 0 and kept == 1
        summary = await owner.fetchrow(
            "SELECT * FROM audit_event WHERE entity='audit' AND action='delete' "
            "ORDER BY seq DESC LIMIT 1"
        )
        assert summary["actor_id"] == "system" and files[0].name in summary["summary"]

    async def test_the_floor_refuses_a_dangerous_line(self, owner, tmp_path):
        seq = await plant_audit(owner, days_old=30, summary="treinta dias")
        done = run_script(
            "app.scripts.audit_archive", "--older-than-days", "10", **archive_env(tmp_path)
        )
        assert done.returncode == 2
        assert list(tmp_path.iterdir()) == []
        assert await owner.fetchval("SELECT count(*) FROM audit_event WHERE seq=$1", seq) == 1

    async def test_the_runtime_role_cannot_do_it_and_nothing_is_written(self, owner, tmp_path):
        await ensure_role(
            owner,
            RUNTIME_ROLE,
            [
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {RUNTIME_ROLE}",
                f"REVOKE ALL ON audit_event FROM {RUNTIME_ROLE}",
                f"GRANT SELECT, INSERT ON audit_event TO {RUNTIME_ROLE}",
            ],
        )
        seq = await plant_audit(owner, days_old=500, summary="intocable")
        done = run_script(
            "app.scripts.audit_archive",
            **archive_env(tmp_path, MAINTENANCE_DATABASE_URL=as_role(RUNTIME_ROLE)),
        )
        assert done.returncode == 3, done.stdout + done.stderr
        assert list(tmp_path.iterdir()) == []  # refused BEFORE writing an archive
        assert await owner.fetchval("SELECT count(*) FROM audit_event WHERE seq=$1", seq) == 1

    async def test_the_maintenance_role_has_exactly_what_the_script_needs(self, owner, tmp_path):
        """Same grants as docker/postgres/audit_maintenance.sql."""
        sql = (ROOT / "docker/postgres/audit_maintenance.sql").read_text()
        assert (
            "GRANT SELECT, INSERT, DELETE ON audit_event" in sql
            and "UPDATE" not in sql.split("GRANT", 1)[1]
        )
        await ensure_role(
            owner, MAINT_ROLE, [f"GRANT SELECT, INSERT, DELETE ON audit_event TO {MAINT_ROLE}"]
        )
        seq = await plant_audit(owner, days_old=700, summary="para el rol de mantenimiento")
        done = run_script(
            "app.scripts.audit_archive",
            **archive_env(tmp_path, MAINTENANCE_DATABASE_URL=as_role(MAINT_ROLE)),
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert await owner.fetchval("SELECT count(*) FROM audit_event WHERE seq=$1", seq) == 0
        # ...and it can do nothing else with that connection.
        conn = await asyncpg.connect(as_role(MAINT_ROLE).replace("+asyncpg", ""))
        try:
            for sql_text in (
                "UPDATE audit_event SET summary='x'",
                "TRUNCATE audit_event",
                "DELETE FROM notification",
                "SELECT * FROM app_user",
            ):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute(sql_text)
        finally:
            await conn.close()

    async def test_dry_run_and_disabled_touch_nothing(self, owner, tmp_path):
        seq = await plant_audit(owner, days_old=900, summary="seguro")
        dry = run_script("app.scripts.audit_archive", "--dry-run", **archive_env(tmp_path))
        off = run_script(
            "app.scripts.audit_archive", **archive_env(tmp_path, AUDIT_RETENTION_DAYS="0")
        )
        assert dry.returncode == 0 and off.returncode == 0
        assert list(tmp_path.iterdir()) == []
        assert await owner.fetchval("SELECT count(*) FROM audit_event WHERE seq=$1", seq) == 1

    async def test_the_api_runtime_still_cannot_delete(self, owner):
        """Retention did not loosen the runtime role: the privilege test of the foundation."""
        await ensure_role(
            owner,
            RUNTIME_ROLE,
            [
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {RUNTIME_ROLE}",
                f"REVOKE ALL ON audit_event FROM {RUNTIME_ROLE}",
                f"GRANT SELECT, INSERT ON audit_event TO {RUNTIME_ROLE}",
            ],
        )
        conn = await asyncpg.connect(as_role(RUNTIME_ROLE).replace("+asyncpg", ""))
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("DELETE FROM audit_event")
        finally:
            await conn.close()
