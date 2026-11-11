"""Every mounted route must be guarded. Verified over HTTP through the OpenAPI document."""

import re

import pytest

from tests.e2e.conftest import API

PUBLIC = {
    ("get", "/health"),
    ("get", "/health/ready"),
    ("post", f"{API}/auth/login/"),
    ("post", f"{API}/auth/refresh/"),
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
ZERO = "00000000-0000-0000-0000-000000000000"


async def _routes(anon):
    r = await anon.get("/openapi.json")
    assert r.status_code == 200
    out = []
    for path, item in r.json()["paths"].items():
        for method in item:
            if method in HTTP_METHODS:
                out.append((method, path))
    return out


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", ZERO, path)


class TestRouteRegistry:
    async def test_openapi_lists_the_expected_surface(self, anon):
        routes = await _routes(anon)
        assert len(routes) >= 30

    async def test_every_non_public_route_answers_401_without_token(self, anon):
        failures = []
        for method, path in await _routes(anon):
            if (method, path) in PUBLIC:
                continue
            r = await anon.request(method.upper(), _fill(path), json={})
            if r.status_code != 401:
                failures.append((method, path, r.status_code))
        assert not failures, failures

    async def test_public_routes_do_not_demand_a_token(self, anon):
        assert (await anon.get("/health")).status_code == 200
        assert (await anon.get("/health/ready")).status_code == 200
        assert (
            await anon.post(f"{API}/auth/login/", json={"email": "a", "password": "b"})
        ).status_code == 401

    async def test_unknown_route_does_not_leak_existence_to_anonymous(self, anon):
        r = await anon.get(f"{API}/this-route-does-not-exist/")
        assert r.status_code in (401, 404)

    async def test_every_route_rejects_garbage_token(self, make_api, anon):
        bad = make_api("garbage")
        for method, path in await _routes(anon):
            if (method, path) in PUBLIC:
                continue
            r = await bad.request(method.upper(), _fill(path), json={})
            assert r.status_code == 401, (method, path, r.status_code)

    async def test_no_route_is_open_to_a_pilot_by_accident(self, pilot, anon):
        """The pilot holds only ticket permissions: admin routes must answer 403."""
        allowed_for_authenticated = {
            ("get", f"{API}/auth/me/"),
            ("patch", f"{API}/auth/me/"),
            ("post", f"{API}/auth/logout/"),
            ("post", f"{API}/auth/password/"),
            ("get", f"{API}/auth/me/signature/"),
            ("patch", f"{API}/auth/me/signature/"),
            ("get", f"{API}/modules/"),
            # Pickers and the day's rate: read-only helpers of the ticket form.
            ("get", f"{API}/user-options/"),
            ("get", f"{API}/exchange-rate/"),
            ("get", f"{API}/catalog-items/"),
            ("get", f"{API}/catalog-items/{{id}}/"),
            # The pilot works tickets, so it reads the forms and the timeline of its own.
            ("get", f"{API}/ticket-types/"),
            ("get", f"{API}/ticket-types/{{id}}/"),
            ("get", f"{API}/tickets/"),
            ("get", f"{API}/tickets/{{id}}/"),
            ("post", f"{API}/tickets/"),
            ("patch", f"{API}/tickets/{{id}}/"),
            ("get", f"{API}/tickets/{{id}}/history/"),
            ("post", f"{API}/tickets/{{id}}/decisions/"),
            ("get", f"{API}/tickets/{{id}}/extractions/"),
            ("post", f"{API}/tickets/{{id}}/extractions/"),
            ("post", f"{API}/tickets/{{id}}/extractions/{{extraction_id}}/validate/"),
            # Closing summary and manifest follow ticket scope; the passenger search is scoped
            # in the query (404 when the caller reads no operation).
            ("get", f"{API}/tickets/{{id}}/closing/"),
            ("get", f"{API}/tickets/{{id}}/passengers/"),
            ("put", f"{API}/tickets/{{id}}/passengers/"),
            ("post", f"{API}/tickets/{{id}}/passengers/"),
            # Attachments follow ticket scope (404 outside it); notices are the caller's own.
            ("get", f"{API}/tickets/{{id}}/attachments/"),
            ("post", f"{API}/tickets/{{id}}/attachments/"),
            ("get", f"{API}/tickets/{{id}}/attachments/{{file_id}}/download/"),
            ("post", f"{API}/tickets/{{id}}/attachments/{{file_id}}/validate/"),
            ("delete", f"{API}/tickets/{{id}}/attachments/{{file_id}}/"),
            ("post", f"{API}/attachments/extract/"),
            ("get", f"{API}/attachments/pending/"),
            ("get", f"{API}/notifications/"),
            ("patch", f"{API}/notifications/{{id}}/"),
            ("post", f"{API}/notifications/read-all/"),
        }
        for method, path in await _routes(anon):
            if (method, path) in PUBLIC or (method, path) in allowed_for_authenticated:
                continue
            if method == "post" and path.endswith("/auth/logout/"):
                continue
            r = await pilot.request(method.upper(), _fill(path), json={})
            assert r.status_code == 403, (method, path, r.status_code)
