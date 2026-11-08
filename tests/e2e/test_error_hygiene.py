"""4xx/5xx bodies never carry tracebacks, SQL, file paths or driver messages."""

import re

from tests.e2e.conftest import API, forge_token, role_by_code, role_id

LEAK = re.compile(
    r"(Traceback|File \"|\.py\"|sqlalchemy|asyncpg|psycopg|SELECT |INSERT |UPDATE .* SET|DETAIL:|"
    r"violates|constraint|relation \"|IntegrityError|ProgrammingError|site-packages|/home/)",
    re.IGNORECASE,
)
ZERO = "00000000-0000-0000-0000-000000000000"


def assert_clean(response):
    assert not LEAK.search(response.text), response.text
    if response.status_code >= 400:
        body = response.json()
        assert {"title", "detail"} <= body.keys(), body


class TestNoLeakage:
    async def test_validation_errors(self, admin):
        for path, body in (
            (f"{API}/roles/", {"name": 5}),
            (f"{API}/users/", {"email": 1}),
            (f"{API}/catalog-items/", {"catalog": []}),
        ):
            r = await admin.post(path, json=body)
            assert r.status_code == 422
            assert_clean(r)

    async def test_malformed_json(self, admin):
        r = await admin.post(
            f"{API}/roles/", content=b"{{{", headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 400
        assert_clean(r)

    async def test_wrong_content_type_does_not_500(self, admin):
        r = await admin.post(
            f"{API}/roles/", content=b"name=abc", headers={"Content-Type": "text/plain"}
        )
        assert r.status_code in (400, 415, 422)
        assert_clean(r)

    async def test_not_found(self, admin):
        for path in (
            f"{API}/users/{ZERO}/",
            f"{API}/roles/{ZERO}/",
            f"{API}/catalog-items/{ZERO}/",
            f"{API}/nope/",
        ):
            r = await admin.get(path)
            assert r.status_code == 404
            assert_clean(r)

    async def test_method_not_allowed(self, admin):
        r = await admin.request("PUT", f"{API}/roles/")
        assert r.status_code == 405
        assert_clean(r)

    async def test_unauthorised_and_forbidden(self, anon, pilot):
        assert_clean(await anon.get(f"{API}/users/"))
        assert_clean(await pilot.get(f"{API}/users/"))

    async def test_conflicts_do_not_expose_constraint_names(self, admin, uniq):
        name = f"Rol {uniq()}"
        await admin.post(f"{API}/roles/", json={"name": name})
        r = await admin.post(f"{API}/roles/", json={"name": name})
        assert r.status_code == 422
        assert_clean(r)

    async def test_duplicate_email_message_is_generic(self, admin, uniq):
        pid = await role_id(admin, "pilot")
        r = await admin.post(
            f"{API}/users/",
            json={"full_name": "X Y", "email": "super@transvalor.com", "role_id": pid},
        )
        assert r.status_code == 422
        assert_clean(r)

    async def test_injection_strings_are_inert(self, admin):
        payloads = [
            "'; DROP TABLE app_user; --",
            "' OR '1'='1",
            "%00",
            "\\x00",
            "{{7*7}}",
            "<script>alert(1)</script>",
        ]
        for p in payloads:
            for path in (
                f"{API}/users/?q={p}",
                f"{API}/roles/?q={p}",
                f"{API}/audit/?search={p}",
                f"{API}/catalog-items/?q={p}",
            ):
                r = await admin.get(path)
                assert r.status_code in (200, 400, 422), (path, r.status_code)
                assert_clean(r)
        assert (await admin.get(f"{API}/users/")).status_code == 200

    async def test_sql_in_filters_values(self, admin):
        r = await admin.get(f"{API}/users/?role_id=1 OR 1=1")
        assert r.status_code == 422
        assert_clean(r)

    async def test_nul_byte_in_json_string_does_not_500(self, admin):
        r = await admin.post(f"{API}/roles/", json={"name": "Rol\u0000 raro"})
        assert r.status_code in (201, 422)
        assert_clean(r)

    async def test_huge_values_do_not_500(self, admin):
        r = await admin.post(f"{API}/roles/", json={"name": "x" * 100_000})
        assert r.status_code == 422
        assert_clean(r)

    async def test_deep_json_does_not_500(self, admin):
        r = await admin.post(
            f"{API}/roles/", json={"name": "ok name", "permissions": [[[[["a"]]]]]}
        )
        assert r.status_code == 422
        assert_clean(r)

    async def test_invalid_pagination(self, admin):
        for q in ("page=0", "page=-1", "limit=0", "limit=abc", "page=99999999999999999999"):
            r = await admin.get(f"{API}/roles/?{q}")
            assert r.status_code in (200, 422), q
            assert_clean(r)

    async def test_huge_page_returns_empty_not_error(self, admin):
        r = await admin.get(f"{API}/roles/?page=100000")
        assert r.status_code == 200 and r.json()["data"] == []

    async def test_token_garbage_variants(self, make_api):
        for token in ("", "a", "a.b", "a.b.c", "x" * 5000):
            r = await make_api(token).get(
                f"{API}/auth/me/", headers={"Authorization": f"Bearer {token}"}
            )
            assert r.status_code == 401
            assert_clean(r)

    async def test_error_responses_are_json(self, admin, anon):
        for r in (await admin.get(f"{API}/nope/"), await anon.get(f"{API}/users/")):
            assert r.headers["content-type"].startswith("application/json")

    async def test_500_shape_is_generic_when_db_constraint_fires(self, admin, uniq):
        """Concurrent duplicate creates may race to the unique index: still a clean 4xx."""
        import asyncio

        name = f"Carrera {uniq()}"
        results = await asyncio.gather(
            *[admin.post(f"{API}/roles/", json={"name": name}) for _ in range(4)]
        )
        codes = sorted(r.status_code for r in results)
        assert codes.count(201) == 1, codes
        for r in results:
            assert r.status_code < 500
            assert_clean(r)

    async def test_openapi_does_not_expose_internal_models(self, anon):
        text = (await anon.get("/openapi.json")).text
        assert "password_hash" not in text


class TestPendingQueueLimit:
    """GET /attachments/pending/ caps the page at 50 (every other listing caps at 100)."""

    async def test_the_maximum_page_is_accepted(self, admin):
        r = await admin.get(f"{API}/attachments/pending/", params={"limit": 50})
        assert r.status_code == 200
        assert r.json()["limit"] == 50

    async def test_over_the_maximum_is_a_clean_spanish_422(self, admin):
        r = await admin.get(f"{API}/attachments/pending/", params={"limit": 51})
        assert r.status_code == 422
        assert_clean(r)
        body = r.json()
        assert body["detail"].startswith("Revisa los campos marcados")
        assert "limit" in body["detail"]
        for message in body["errors"].values():
            assert not re.search(
                r"(less than|greater than|input should|ensure this)", message, re.I
            )
            assert re.search(
                r"[áéíóúñ]|valor|campo|inválid|no válid|máxim|mayor|menor", message, re.I
            )
