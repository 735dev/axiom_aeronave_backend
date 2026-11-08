"""A user flagged `must_change_password` can only change it, read /auth/me and log out."""

import re

import pytest

from tests.e2e.conftest import (
    API,
    Api,
    create_user,
    issue_temporary,
    role_id,
)
from tests.e2e.tickets_support import make_operation

ME = f"{API}/auth/me/"
PASSWORD = f"{API}/auth/password/"
LOGOUT = f"{API}/auth/logout/"
GHOST = "00000000-0000-0000-0000-000000000000"
OPEN_TO_THE_FLAGGED = {("GET", ME), ("POST", PASSWORD), ("POST", LOGOUT)}
PUBLIC = {
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("POST", f"{API}/auth/login/"),
    ("POST", f"{API}/auth/refresh/"),
}
CODE = "cambio_contrasena"


@pytest.fixture
async def flagged(admin, uniq):
    """A superuser holding a temporary password: every permission, still locked out."""
    user = await create_user(admin, f"{uniq('f')}@example.com", await role_id(admin, "superuser"))
    temp = await issue_temporary(admin, user["id"])
    client = Api(admin.app)
    login = await client.login(user["email"], temp)
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True
    return client.as_token(login.json()["token"]), user, temp, login.json()


async def operations(app) -> list[tuple[str, str, str]]:
    """(method, concrete path, original template) of every route the API mounts."""
    schema = (await Api(app).get("/openapi.json")).json()
    found = []
    for template, item in schema["paths"].items():
        for method in item:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                concrete = re.sub(r"\{[^}]+\}", GHOST, template)
                found.append((method.upper(), concrete, template))
    return found


class TestBlockedUntilChanged:
    async def test_every_route_but_three_answers_403_with_the_code(self, flagged, app):
        api, _, _, _ = flagged
        checked = 0
        for method, path, _ in await operations(app):
            if (method, path) in OPEN_TO_THE_FLAGGED | PUBLIC:
                continue
            r = await api.request(method, path, json={} if method != "GET" else None)
            assert r.status_code == 403, (method, path, r.status_code)
            body = r.json()
            assert body["codigo"] == CODE and "contraseña" in body["detail"].lower()
            checked += 1
        assert checked > 60  # the whole API, not a sample

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", f"{API}/tickets/"),
            ("POST", f"{API}/tickets/"),
            ("PATCH", f"{API}/tickets/{GHOST}/"),
            ("GET", f"{API}/tickets/{GHOST}/attachments/"),
            ("POST", f"{API}/tickets/{GHOST}/attachments/"),
            ("PUT", f"{API}/tickets/{GHOST}/passengers/"),
            ("GET", f"{API}/passengers/search/"),
            ("POST", f"{API}/attachments/extract/"),
            ("GET", f"{API}/notifications/"),
            ("POST", f"{API}/notifications/read-all/"),
            ("GET", f"{API}/audit/"),
            ("GET", f"{API}/audit/export/"),
            ("GET", f"{API}/users/"),
            ("POST", f"{API}/users/{GHOST}/reset-password/"),
            ("GET", f"{API}/roles/"),
            ("POST", f"{API}/roles/"),
            ("GET", f"{API}/modules/"),
            ("GET", f"{API}/catalog-items/"),
            ("PATCH", f"{API}/auth/me/"),
            ("PATCH", f"{API}/auth/me/signature/"),
            ("GET", f"{API}/reconciliation/"),
            ("GET", f"{API}/ticket-types/"),
        ],
    )
    async def test_each_class_of_route_is_closed(self, flagged, method, path):
        api, _, _, _ = flagged
        r = await api.request(method, path, json={} if method != "GET" else None)
        assert r.status_code == 403 and r.json()["codigo"] == CODE

    async def test_a_flagged_session_cannot_even_read_what_its_role_allows(self, flagged, admin):
        api, _, _, _ = flagged
        assert (await admin.get(f"{API}/users/")).status_code == 200
        assert (await api.get(f"{API}/users/")).status_code == 403

    async def test_the_three_open_routes_work(self, flagged):
        api, user, _, _ = flagged
        me = await api.get(ME)
        assert me.status_code == 200 and me.json()["user"]["must_change_password"] is True
        assert (await api.post(LOGOUT)).status_code == 204

    async def test_refresh_keeps_working_and_keeps_the_flag(self, flagged):
        api, _, _, login = flagged
        r = await api.post(f"{API}/auth/refresh/", json={"refresh_token": login["refresh_token"]})
        assert r.status_code == 200
        fresh = api.as_token(r.json()["token"])
        assert (await fresh.get(ME)).json()["user"]["must_change_password"] is True
        assert (await fresh.get(f"{API}/users/")).status_code == 403

    async def test_the_admin_listing_shows_the_flag(self, flagged, admin):
        _, user, _, _ = flagged
        shown = (await admin.get(f"{API}/users/{user['id']}/")).json()
        assert shown["must_change_password"] is True

    async def test_a_failed_change_leaves_the_account_locked(self, flagged):
        api, _, temp, _ = flagged
        r = await api.post(
            PASSWORD, json={"current_password": "otra-cosa-123", "new_password": "Nueva-clave-99"}
        )
        assert r.status_code == 422
        weak = await api.post(PASSWORD, json={"current_password": temp, "new_password": "corta"})
        assert weak.status_code == 422
        assert (await api.get(f"{API}/users/")).status_code == 403


class TestChangingItOpensEverything:
    async def test_after_the_change_the_same_routes_are_open(self, flagged, admin, app):
        api, user, temp, _ = flagged
        changed = await api.post(
            PASSWORD, json={"current_password": temp, "new_password": temp[:18] + "-Zeta9"}
        )
        assert changed.status_code == 200
        fresh = api.as_token(changed.json()["token"])
        me = await fresh.get(ME)
        assert me.json()["user"]["must_change_password"] is False
        for method, path, _ in await operations(app):
            if method != "GET" or "{" in path or (method, path) in PUBLIC:
                continue
            if "/audit/export" in path or "/health" in path or "openapi" in path:
                continue
            r = await fresh.get(path)
            assert not (r.status_code == 403 and r.json().get("codigo") == CODE), path
        assert (await fresh.get(f"{API}/users/")).status_code == 200
        assert (await fresh.get(f"{API}/tickets/")).status_code == 200

    async def test_the_temporary_session_dies_when_the_password_changes(self, flagged):
        api, _, temp, _ = flagged
        await api.post(
            PASSWORD, json={"current_password": temp, "new_password": temp[:18] + "-Zeta9"}
        )
        assert (await api.get(ME)).status_code == 401

    async def test_a_new_reset_locks_a_cleared_account_again(self, flagged, admin):
        api, user, temp, _ = flagged
        changed = await api.post(
            PASSWORD, json={"current_password": temp, "new_password": temp[:18] + "-Zeta9"}
        )
        fresh = api.as_token(changed.json()["token"])
        assert (await fresh.get(f"{API}/users/")).status_code == 200
        again = await issue_temporary(admin, user["id"])
        assert (await fresh.get(ME)).status_code == 401  # the reset revoked the session
        relogin = await Api(admin.app).login(user["email"], again)
        locked = Api(admin.app, token=relogin.json()["token"])
        assert (await locked.get(f"{API}/users/")).status_code == 403

    async def test_an_admin_ticket_flow_is_not_affected_for_unflagged_users(self, admin):
        op = await make_operation(admin)
        assert (await admin.get(f"{API}/tickets/{op['id']}/")).status_code == 200
