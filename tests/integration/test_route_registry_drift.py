"""The registry and the mounted routes must never drift apart (an unregistered route
would be reachable without a permission check)."""

import json

import pytest
from fastapi import APIRouter, FastAPI

from app.main import app
from app.utils.endpoint_registry import (
    REGISTRY_PATH,
    assert_routes_registered,
    load_registry,
    mounted_keys,
)


class TestRegistry:
    def test_real_app_is_in_sync(self):
        assert_routes_registered(app)

    def test_every_entry_has_a_valid_access_level(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        for key, rule in raw.items():
            assert rule["access"] in {"public", "authenticated", "permission"}, key
            assert (rule["access"] == "permission") == bool(rule.get("permissions")), key

    def test_permission_codes_in_registry_exist_in_seed(self):
        from app.utils.seed_loader import load_seeds

        codes = {p["code"] for p in load_seeds().permissions}
        for key, rule in load_registry().items():
            for code in rule.permissions:
                assert code in codes, (key, code)

    def test_public_routes_are_only_the_expected_ones(self):
        public = {k for k, r in load_registry().items() if r.access.value == "public"}
        assert public == {
            "GET /health",
            "GET /health/ready",
            "POST /api/v1/auth/login/",
            "POST /api/v1/auth/refresh/",
        }

    def test_unregistered_route_fails_startup_check(self):
        rogue = FastAPI()
        router = APIRouter()

        @router.get("/api/v1/rogue/")
        async def rogue_route():  # pragma: no cover - never called
            return {}

        rogue.include_router(router)
        with pytest.raises(RuntimeError, match="Unregistered"):
            assert_routes_registered(rogue)

    def test_stale_entry_fails_startup_check(self):
        empty = FastAPI()
        with pytest.raises(RuntimeError, match="not mounted"):
            assert_routes_registered(empty)

    def test_no_module_level_route_decorators_outside_cbv(self):
        assert mounted_keys(app)
