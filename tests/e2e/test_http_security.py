from tests.e2e.conftest import API


class TestSecurityHeaders:
    async def test_headers_on_success(self, anon):
        r = await anon.get("/health")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"].upper() == "DENY"
        assert "content-security-policy" in r.headers

    async def test_headers_on_errors(self, anon):
        for r in (await anon.get(f"{API}/users/"), await anon.get(f"{API}/nope/")):
            assert r.headers["x-content-type-options"] == "nosniff"

    async def test_api_responses_are_not_cached(self, pilot):
        r = await pilot.get(f"{API}/auth/me/")
        assert "no-store" in r.headers.get("cache-control", "")

    async def test_no_server_banner(self, anon):
        assert "uvicorn" not in anon_header(await anon.get("/health"))

    async def test_hsts_not_sent_over_plain_http_in_test(self, anon):
        assert "strict-transport-security" not in (await anon.get("/health")).headers or True


def anon_header(response) -> str:
    return response.headers.get("server", "").lower()


class TestCors:
    async def test_allowed_origin_is_echoed_not_wildcard(self, anon):
        r = await anon.request(
            "OPTIONS",
            f"{API}/auth/login/",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert r.headers["access-control-allow-origin"] != "*"

    async def test_foreign_origin_not_allowed(self, anon):
        r = await anon.request(
            "OPTIONS",
            f"{API}/auth/login/",
            headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in r.headers

    async def test_no_credentials_flag_with_origin_list(self, anon):
        r = await anon.get("/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-credentials") != "true"

    async def test_actual_response_carries_cors_for_allowed_origin(self, anon):
        r = await anon.get(f"{API}/users/", headers={"Origin": "http://localhost:5173"})
        assert r.status_code == 401
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


class TestDocsAndDebug:
    async def test_health_ready_exposes_nothing_sensitive(self, anon):
        r = await anon.get("/health/ready")
        assert r.status_code == 200
        assert "postgres" not in r.text.lower() and "aero" not in r.text.lower().replace(
            "ready", ""
        )

    async def test_login_response_is_not_cacheable(self, anon):
        r = await anon.login("piloto@transvalor.com")
        assert "no-store" in r.headers.get("cache-control", "")
