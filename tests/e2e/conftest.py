"""Black-box E2E harness.

The conftest is the only place that touches the application: it points it at a throwaway
database, builds the schema with the project's own Alembic migration, runs the project's
own seed script and then exposes the ASGI app. Test files talk HTTP only.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://aero:aero@localhost:5440/aero_test"
)
# Optional: run the whole suite as the least-privilege runtime role (no UPDATE/DELETE on
# audit_event), e.g. TEST_APP_DATABASE_URL=postgresql+asyncpg://aero_app:pw1@localhost:5440/aero_test
APP_DB_URL = os.environ.get("TEST_APP_DATABASE_URL") or TEST_DB_URL
os.environ.update(
    {
        "DATABASE_URL": APP_DB_URL,
        "MIGRATION_DATABASE_URL": TEST_DB_URL,
        "JWT_SECRET": "test-secret-test-secret-test-secret-0123456789",
        "ENVIRONMENT": "test",
        "RUNNING_TESTS": "true",
        "BCRYPT_ROUNDS": "4",
        "DEBUG": "false",
        "ALLOWED_ORIGINS": "http://localhost:5173",
        "SEED_USER_PASSWORD": "",
        "REDIS_URL": "",
        # Attachments: an in-process Blackbox and receipt reader, never a real provider.
        "BLACKBOX_MODE": "fake",
        "BLACKBOX_RETRY_BASE_DELAY": "0.001",
        "RECEIPT_READER": "fake",
        "EXCHANGE_RATE_MODE": "fake",
        # The in-process app runs the whole suite from a handful of accounts: its action
        # budgets are lifted. tests/e2e/test_rate_limits.py runs a server with real ones.
        "RATE_LIMIT_EXTRACT_PER_ACCOUNT": "100000",
        "RATE_LIMIT_EXTRACT_PER_IP": "100000",
        "RATE_LIMIT_UPLOAD_PER_ACCOUNT": "100000",
        "RATE_LIMIT_UPLOAD_PER_IP": "100000",
        "RATE_LIMIT_SEARCH_PER_ACCOUNT": "100000",
        "RATE_LIMIT_SEARCH_PER_IP": "100000",
        "RATE_LIMIT_EXPORT_PER_ACCOUNT": "100000",
        "RATE_LIMIT_EXPORT_PER_IP": "100000",
        "RATE_LIMIT_IMPORT_PER_ACCOUNT": "100000",
        "RATE_LIMIT_IMPORT_PER_IP": "100000",
        "RATE_LIMIT_AUDIT_SEARCH_PER_ACCOUNT": "100000",
        "RATE_LIMIT_AUDIT_SEARCH_PER_IP": "100000",
        "RATE_LIMIT_HEAVY_LIST_PER_ACCOUNT": "100000",
        "RATE_LIMIT_HEAVY_LIST_PER_IP": "100000",
    }
)

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

DEMO_PASSWORD = "demo1234"
API = "/api/v1"


def _plain_dsn() -> str:
    return TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://")


async def _reset_schema() -> None:
    conn = await asyncpg.connect(_plain_dsn())
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


async def _grant_runtime_role() -> None:
    """Same grants as docker/postgres/audit_grants.sql (that file needs psql variables)."""
    from urllib.parse import urlparse

    role = urlparse(APP_DB_URL).username
    conn = await asyncpg.connect(_plain_dsn())
    try:
        for statement in (
            f'GRANT USAGE ON SCHEMA public TO "{role}"',
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"',
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"',
            f'REVOKE ALL ON audit_event FROM "{role}"',
            f'GRANT SELECT, INSERT ON audit_event TO "{role}"',
        ):
            await conn.execute(statement)
    finally:
        await conn.close()


def _run(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": TEST_DB_URL},  # migrations and seed run as owner
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    asyncio.run(_reset_schema())
    _run("-m", "alembic", "upgrade", "head")
    _run("-m", "app.scripts.init")
    if APP_DB_URL != TEST_DB_URL:
        asyncio.run(_grant_runtime_role())


@pytest.fixture(scope="session")
def app(database):
    from app.main import app as application

    return application


_ip_counter = itertools.count(1)


def fresh_ip() -> str:
    n = next(_ip_counter)
    return f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"


class Api:
    """Thin HTTP helper bound to one client IP and (optionally) one bearer token."""

    def __init__(self, app, ip: str | None = None, token: str | None = None):
        self.app = app
        self.ip = ip or fresh_ip()
        self.token = token

    def _client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, client=(self.ip, 50000))
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.token}"
        async with self._client() as client:
            return await client.request(method, path, headers=headers, **kwargs)

    async def get(self, path, **kw):
        return await self.request("GET", path, **kw)

    async def post(self, path, **kw):
        return await self.request("POST", path, **kw)

    async def patch(self, path, **kw):
        return await self.request("PATCH", path, **kw)

    async def delete(self, path, **kw):
        return await self.request("DELETE", path, **kw)

    async def login(self, email: str, password: str = DEMO_PASSWORD) -> httpx.Response:
        return await self.post(f"{API}/auth/login/", json={"email": email, "password": password})

    def as_token(self, token: str) -> "Api":
        return Api(self.app, self.ip, token)


@pytest.fixture
def anon(app) -> Api:
    return Api(app)


@pytest.fixture(scope="session")
def make_api(app):
    def factory(token: str | None = None, ip: str | None = None) -> Api:
        return Api(app, ip, token)

    return factory


@pytest_asyncio.fixture
async def logged(app):
    """`await logged('piloto@transvalor.com')` -> (Api with a fresh token, login body)."""

    async def do(email: str, password: str = DEMO_PASSWORD):
        client = Api(app)
        response = await client.login(email, password)
        assert response.status_code == 200, response.text
        body = response.json()
        return Api(app, client.ip, body["token"]), body

    return do


@pytest_asyncio.fixture
async def admin(logged) -> Api:
    api, _ = await logged("super@transvalor.com")
    return api


@pytest_asyncio.fixture
async def pilot(logged) -> Api:
    api, _ = await logged("piloto@transvalor.com")
    return api


JWT_SECRET = os.environ["JWT_SECRET"]
PERMISSION_CODES = {
    "tickets.view",
    "tickets.create",
    "tickets.edit",
    "tickets.decide_any",
    "tickets.sign",
    "passengers.view",
    "ticket_types.manage",
    "notifications.view",
    "users.view",
    "users.manage",
    "roles.view",
    "roles.manage",
    "catalogs.view",
    "catalogs.manage",
    "analytics.view",
    "reconciliation.close",
    "audit.view",
    "audit.export",
}

SEEDED_TYPE_CODES = ("operation", "expense", "technical_report", "inac_permit", "hangar_incident")
DERIVED_PERMISSION_CODES = {
    f"{base}.{code}"
    for base in ("tickets.view", "tickets.create", "tickets.edit")
    for code in SEEDED_TYPE_CODES
}
FULL_CATALOG = PERMISSION_CODES | DERIVED_PERMISSION_CODES


def forge_token(
    sub: str,
    *,
    ttl: int = 600,
    token_type: str = "access",
    secret: str | None = None,
    iat_offset: float = 0.0,
) -> str:
    """A JWT signed by the test (not by the app): used to probe token validation."""
    import time

    import jwt

    now = time.time() + iat_offset
    payload = {"sub": sub, "iat": now, "exp": int(now + ttl), "token_type": token_type}
    return jwt.encode(payload, secret or JWT_SECRET, algorithm="HS256")


def png_data_url(size: int = 200, width: int = 10, height: int = 10) -> str:
    import base64
    import struct

    body = (
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    )
    body += b"\x08\x06\x00\x00\x00"
    body += b"\x00" * max(0, size - len(body))
    return "data:image/png;base64," + base64.b64encode(body).decode()


def jpeg_data_url(size: int = 200) -> str:
    import base64

    body = b"\xff\xd8\xff\xe0" + b"\x00" * max(0, size - 4)
    return "data:image/jpeg;base64," + base64.b64encode(body).decode()


async def create_role(api: "Api", name: str, permissions: list[str] | None = None) -> dict:
    r = await api.post(f"{API}/roles/", json={"name": name, "permissions": permissions or []})
    assert r.status_code == 201, r.text
    return r.json()


async def create_user(api: "Api", email: str, role_id: str, **extra) -> dict:
    payload = {"full_name": "Usuario Prueba", "email": email, "role_id": role_id, **extra}
    r = await api.post(f"{API}/users/", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def issue_temporary(api: "Api", user_id: str) -> str:
    """Issue a temporary password through the API and return it. The account is flagged
    `must_change_password`: with it only change-password, /auth/me and logout work."""
    r = await api.post(f"{API}/users/{user_id}/reset-password/")
    assert r.status_code == 200, r.text
    return r.json()["temporary_password"]


async def activate(api: "Api", user_id: str) -> str:
    """Issue a temporary password and complete the forced change; return the usable password."""
    temporary = await issue_temporary(api, user_id)
    client = Api(api.app)
    logged_in = await client.login(await _email_of(api, user_id), temporary)
    assert logged_in.status_code == 200, logged_in.text
    fresh = temporary[:20] + "-Ok1"
    changed = await client.as_token(logged_in.json()["token"]).post(
        f"{API}/auth/password/", json={"current_password": temporary, "new_password": fresh}
    )
    assert changed.status_code == 200, changed.text
    return fresh


async def _email_of(api: "Api", user_id: str) -> str:
    r = await api.get(f"{API}/users/{user_id}/")
    assert r.status_code == 200, r.text
    return r.json()["email"]


@pytest.fixture
def uniq():
    import uuid

    return lambda prefix="x": f"{prefix}{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def new_account(admin, logged, uniq):
    """Create a user with a role holding `permissions`; return (Api, user, password)."""

    async def make(permissions: list[str], **extra):
        role = await create_role(admin, f"Rol {uniq()}", permissions)
        email = f"{uniq('u')}@example.com"
        user = await create_user(admin, email, role["id"], **extra)
        password = await activate(admin, user["id"])
        api, _ = await logged(email, password)
        return api, user, password

    return make


@pytest_asyncio.fixture
async def victim(new_account):
    """A throw-away account: tests that fail logins on purpose never burn a seeded user
    (the per-account lock-out is process-wide)."""
    _, user, password = await new_account(["tickets.view"])
    return user["email"], password


async def role_by_code(api: "Api", code: str) -> dict:
    """Walk the role pages until the role with `code` shows up (tests create many roles)."""
    page = 1
    while True:
        body = (await api.get(f"{API}/roles/?limit=100&page={page}")).json()
        for role in body["data"]:
            if role["code"] == code:
                return role
        if not body["links"]["next"]:
            raise LookupError(code)
        page += 1


async def role_id(api: "Api", code: str) -> str:
    return (await role_by_code(api, code))["id"]


@pytest_asyncio.fixture
async def ops(logged) -> Api:
    api, _ = await logged("operaciones@transvalor.com")
    return api


@pytest_asyncio.fixture
async def maintenance(logged) -> Api:
    api, _ = await logged("mantenimiento@transvalor.com")
    return api


@pytest_asyncio.fixture
async def avsec(logged) -> Api:
    api, _ = await logged("avsec@transvalor.com")
    return api


@pytest_asyncio.fixture
async def general(logged) -> Api:
    """Gerencia General: the last of the four signatures of an operation."""
    api, _ = await logged("gerencia@transvalor.com")
    return api


@pytest_asyncio.fixture
async def ground(logged) -> Api:
    api, _ = await logged("tierra@transvalor.com")
    return api


@pytest_asyncio.fixture
async def accounting(logged) -> Api:
    api, _ = await logged("admin@transvalor.com")
    return api


@pytest_asyncio.fixture
async def director(new_account) -> Api:
    """Management: may authorize flagged expenses but holds no edit permission."""
    api, _, _ = await new_account(["tickets.view", "tickets.decide_any"])
    return api
