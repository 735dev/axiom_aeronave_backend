"""The runtime role of docker/postgres/audit_grants.sql cannot rewrite the trail."""

import asyncpg
import pytest

from tests.integration.conftest import TEST_DB_URL

OWNER_DSN = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://")
ROLE, PASSWORD = "aero_it_runtime", "it-pass"
APP_DSN = OWNER_DSN.replace("//" + OWNER_DSN.split("//")[1].split("@")[0], f"//{ROLE}:{PASSWORD}")

INSERT = (
    "INSERT INTO audit_event (id, actor_id, actor_name, actor_role, action, entity, entity_id, "
    "entity_label, summary) VALUES (gen_random_uuid(), 'a', 'a', 'a', 'create', 'user', '1', '1', 'it')"
)


@pytest.fixture
async def runtime_conn():
    owner = await asyncpg.connect(OWNER_DSN)
    try:
        exists = await owner.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", ROLE)
        if not exists:
            await owner.execute(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'")
        for statement in (
            f"GRANT USAGE ON SCHEMA public TO {ROLE}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE}",
            f"REVOKE ALL ON audit_event FROM {ROLE}",
            f"GRANT SELECT, INSERT ON audit_event TO {ROLE}",
        ):
            await owner.execute(statement)
        conn = await asyncpg.connect(APP_DSN)
        yield conn
        await conn.close()
    finally:
        await owner.close()


class TestRuntimeRole:
    async def test_can_insert_and_read(self, runtime_conn):
        await runtime_conn.execute(INSERT)
        assert await runtime_conn.fetchval("SELECT count(*) FROM audit_event") >= 1

    @pytest.mark.parametrize(
        "sql",
        [
            "UPDATE audit_event SET summary = 'tampered'",
            "DELETE FROM audit_event",
            "TRUNCATE audit_event",
        ],
    )
    async def test_cannot_rewrite_history(self, runtime_conn, sql):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime_conn.execute(sql)

    async def test_still_manages_business_tables(self, runtime_conn):
        await runtime_conn.execute("UPDATE catalog_item SET sort_order = sort_order")
