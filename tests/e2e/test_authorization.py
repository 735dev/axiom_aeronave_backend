"""Each admin endpoint: 401 anonymous, 403 for a role lacking the permission, OK with it."""

import pytest

from tests.e2e.conftest import API

ZERO = "00000000-0000-0000-0000-000000000000"

# (method, path, body, permission that opens it, status expected when allowed and the id is unknown)
CASES = [
    ("GET", f"{API}/users/", None, "users.view"),
    ("GET", f"{API}/users/{ZERO}/", None, "users.view"),
    ("GET", f"{API}/users/{ZERO}/effective-permissions/", None, "users.view"),
    ("POST", f"{API}/users/", {}, "users.manage"),
    ("PATCH", f"{API}/users/{ZERO}/", {}, "users.manage"),
    ("POST", f"{API}/users/{ZERO}/reset-password/", None, "users.manage"),
    ("GET", f"{API}/roles/", None, "roles.view"),
    ("GET", f"{API}/roles/{ZERO}/", None, "roles.view"),
    ("POST", f"{API}/roles/", {}, "roles.manage"),
    ("PATCH", f"{API}/roles/{ZERO}/", {}, "roles.manage"),
    ("DELETE", f"{API}/roles/{ZERO}/", None, "roles.manage"),
    ("GET", f"{API}/permissions/", None, "roles.view"),
    ("PATCH", f"{API}/modules/{ZERO}/", {}, "roles.manage"),
    ("GET", f"{API}/catalogs/", None, "catalogs.view"),
    ("POST", f"{API}/catalog-items/", {}, "catalogs.manage"),
    ("PATCH", f"{API}/catalog-items/{ZERO}/", {}, "catalogs.manage"),
    ("DELETE", f"{API}/catalog-items/{ZERO}/", None, "catalogs.manage"),
    ("GET", f"{API}/audit/", None, "audit.view"),
]
IDS = [f"{m} {p.replace(ZERO, '{id}')}" for m, p, _, _ in CASES]


@pytest.mark.parametrize("method,path,body,permission", CASES, ids=IDS)
class TestAdminEndpointGuards:
    async def test_anonymous_gets_401(self, anon, method, path, body, permission):
        r = await anon.request(method, path, json=body)
        assert r.status_code == 401

    async def test_pilot_gets_403(self, pilot, method, path, body, permission):
        r = await pilot.request(method, path, json=body)
        assert r.status_code == 403
        assert {"title", "detail"} <= r.json().keys()

    async def test_forbidden_body_does_not_reveal_existence(
        self, pilot, method, path, body, permission
    ):
        r = await pilot.request(method, path, json=body)
        assert ZERO not in r.text

    async def test_holder_of_the_permission_passes_the_guard(
        self, new_account, method, path, body, permission
    ):
        api, _, _ = await new_account([permission])
        r = await api.request(method, path, json=body)
        assert r.status_code != 403 and r.status_code != 401, r.text

    async def test_unrelated_permission_is_not_enough(
        self, new_account, method, path, body, permission
    ):
        other = "analytics.view"
        api, _, _ = await new_account([other])
        assert (await api.request(method, path, json=body)).status_code == 403


class TestAuthenticatedOnlyRoutes:
    async def test_roleless_of_permissions_can_use_self_service(self, new_account):
        api, _, _ = await new_account([])
        for path in (
            f"{API}/auth/me/",
            f"{API}/modules/",
            f"{API}/catalog-items/",
            f"{API}/auth/me/signature/",
        ):
            assert (await api.get(path)).status_code == 200, path

    async def test_permission_check_is_any_of(self, new_account):
        for perm in ("roles.view", "roles.manage", "users.view", "users.manage"):
            api, _, _ = await new_account([perm])
            assert (await api.get(f"{API}/roles/")).status_code == 200, perm
