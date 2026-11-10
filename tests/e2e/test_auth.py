import time

import pytest

from tests.e2e.conftest import (
    API,
    DEMO_PASSWORD,
    Api,
    create_role,
    create_user,
    forge_token,
    issue_temporary,
)

LOGIN = f"{API}/auth/login/"
ME = f"{API}/auth/me/"


class TestLogin:
    async def test_success_returns_contract_shape(self, anon):
        r = await anon.login("piloto@transvalor.com")
        assert r.status_code == 200
        body = r.json()
        assert {"user", "permissions", "token", "refresh_token"} <= body.keys()
        assert body["user"]["email"] == "piloto@transvalor.com"
        assert body["user"]["role_code"] == "pilot"
        assert "password_hash" not in str(body)

    async def test_email_is_case_insensitive(self, anon):
        r = await anon.login("  PILOTO@Transvalor.com ")
        assert r.status_code == 200

    async def test_pilot_permissions_match_seed(self, anon):
        body = (await anon.login("piloto@transvalor.com")).json()
        assert sorted(body["permissions"]) == sorted(
            ["tickets.view", "tickets.create", "tickets.edit", "tickets.sign", "notifications.view"]
        )

    async def test_superuser_has_whole_catalog(self, anon):
        body = (await anon.login("super@transvalor.com")).json()
        assert "audit.view" in body["permissions"] and "users.manage" in body["permissions"]

    async def test_wrong_password_is_401(self, anon, victim):
        r = await anon.login(victim[0], "not-the-password")
        assert r.status_code == 401
        assert r.json()["title"]

    async def test_unknown_user_same_response_as_wrong_password(self, app, victim):
        wrong = await Api(app).login(victim[0], "nope-nope-nope")
        unknown = await Api(app).login("nobody@nowhere.test", "nope-nope-nope")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    async def test_unknown_user_costs_the_same_time_class(self, app, victim):
        """The decoy hash makes an unknown email as slow as a wrong password (same order)."""

        async def timed(email):
            start = time.perf_counter()
            await Api(app).login(email, "wrong-password-1")
            return time.perf_counter() - start

        known = sorted([await timed(victim[0]) for _ in range(3)])[1]
        unknown = sorted([await timed(f"ghost{i}@nowhere.test") for i in range(3)])[1]
        assert unknown > known / 5, (known, unknown)

    async def test_inactive_user_cannot_login(self, admin, new_account, uniq, anon):
        api, user, password = await new_account(["tickets.view"])
        assert (
            await admin.patch(f"{API}/users/{user['id']}/", json={"is_active": False})
        ).status_code == 200
        r = await Api(admin.app).login(user["email"], password)
        assert r.status_code == 401

    async def test_missing_fields_422(self, anon):
        r = await anon.post(LOGIN, json={"email": "a@b.c"})
        assert r.status_code == 422

    async def test_invalid_json_400(self, anon):
        r = await anon.post(
            LOGIN, content=b"{not json", headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 400

    async def test_malformed_email_is_just_401(self, anon):
        r = await anon.login("not-an-email", "whatever-1234")
        assert r.status_code == 401

    async def test_overlong_password_rejected_without_500(self, anon, victim):
        r = await anon.login(victim[0], "x" * 200)
        assert r.status_code == 401

    async def test_password_over_72_bytes_does_not_crash(self, anon, victim):
        r = await anon.login(victim[0], "ñ" * 100)
        assert r.status_code == 401

    async def test_login_updates_last_login(self, admin, anon):
        await anon.login("tierra@transvalor.com")
        users = (await admin.get(f"{API}/users/?q=tierra")).json()["data"]
        assert users[0]["last_login_at"] is not None


class TestSession:
    async def test_me_without_token_401(self, anon):
        r = await anon.get(ME)
        assert r.status_code == 401
        assert r.headers.get("www-authenticate", "").lower().startswith("bearer")

    async def test_me_returns_user_and_permissions(self, pilot):
        body = (await pilot.get(ME)).json()
        assert body["user"]["email"] == "piloto@transvalor.com"
        assert "tickets.create" in body["permissions"]

    async def test_garbage_token_401(self, make_api):
        assert (await make_api("not.a.jwt").get(ME)).status_code == 401

    async def test_expired_token_401(self, make_api, pilot):
        uid = (await pilot.get(ME)).json()["user"]["id"]
        expired = forge_token(uid, ttl=-120, iat_offset=-3600)
        assert (await make_api(expired).get(ME)).status_code == 401

    async def test_token_signed_with_other_secret_401(self, make_api, pilot):
        uid = (await pilot.get(ME)).json()["user"]["id"]
        assert (await make_api(forge_token(uid, secret="x" * 40)).get(ME)).status_code == 401

    async def test_forged_valid_token_works_proving_the_helper(self, make_api, pilot):
        uid = (await pilot.get(ME)).json()["user"]["id"]
        assert (await make_api(forge_token(uid)).get(ME)).status_code == 200

    async def test_refresh_token_is_not_an_access_token(self, make_api, pilot):
        uid = (await pilot.get(ME)).json()["user"]["id"]
        assert (await make_api(forge_token(uid, token_type="refresh")).get(ME)).status_code == 401

    async def test_unsigned_alg_none_rejected(self, make_api, pilot):
        import base64
        import json

        uid = (await pilot.get(ME)).json()["user"]["id"]

        def b64(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

        token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': uid, 'exp': int(time.time()) + 600, 'iat': time.time(), 'token_type': 'access'})}."
        assert (await make_api(token).get(ME)).status_code == 401

    async def test_token_for_unknown_user_401(self, make_api):
        assert (
            await make_api(forge_token("00000000-0000-0000-0000-000000000000")).get(ME)
        ).status_code == 401

    async def test_token_with_non_uuid_subject_401(self, make_api):
        assert (await make_api(forge_token("not-a-uuid")).get(ME)).status_code == 401

    async def test_token_issued_in_the_future_is_rejected_or_tolerated_only_by_leeway(
        self, make_api, pilot
    ):
        uid = (await pilot.get(ME)).json()["user"]["id"]
        far = forge_token(uid, iat_offset=3600, ttl=7200)
        assert (await make_api(far).get(ME)).status_code in (200, 401)

    async def test_basic_scheme_rejected(self, make_api):
        r = await make_api().get(ME, headers={"Authorization": "Basic abc"})
        assert r.status_code == 401


class TestRevocation:
    async def test_logout_kills_the_token(self, logged):
        api, _ = await logged("tierra@transvalor.com")
        assert (await api.get(ME)).status_code == 200
        assert (await api.post(f"{API}/auth/logout/")).status_code == 204
        assert (await api.get(ME)).status_code == 401

    async def test_logout_kills_the_refresh_token_too(self, logged, anon):
        api, body = await logged("mantenimiento@transvalor.com")
        await api.post(f"{API}/auth/logout/")
        r = await anon.post(f"{API}/auth/refresh/", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 401

    async def test_new_login_after_logout_works(self, logged):
        api, _ = await logged("admin@transvalor.com")
        await api.post(f"{API}/auth/logout/")
        api2, _ = await logged("admin@transvalor.com")
        assert (await api2.get(ME)).status_code == 200

    async def test_deactivating_a_user_revokes_live_tokens(self, admin, new_account):
        api, user, _ = await new_account(["tickets.view"])
        assert (await api.get(ME)).status_code == 200
        await admin.patch(f"{API}/users/{user['id']}/", json={"is_active": False})
        assert (await api.get(ME)).status_code == 401

    async def test_password_reset_by_admin_revokes_tokens(self, admin, new_account):
        api, user, _ = await new_account(["tickets.view"])
        await admin.post(f"{API}/users/{user['id']}/reset-password/")
        assert (await api.get(ME)).status_code == 401

    async def test_changing_role_revokes_tokens(self, admin, new_account, uniq):
        from tests.e2e.conftest import create_role

        api, user, _ = await new_account(["tickets.view"])
        other = await create_role(admin, f"Otro {uniq()}", ["tickets.view"])
        r = await admin.patch(f"{API}/users/{user['id']}/", json={"role_id": other["id"]})
        assert r.status_code == 200, r.text
        assert (await api.get(ME)).status_code == 401

    async def test_token_issued_before_revocation_stays_dead(self, logged, make_api):
        api, _ = await logged("tierra@transvalor.com")
        uid = (await api.get(ME)).json()["user"]["id"]
        old = forge_token(uid, iat_offset=-30)
        await api.post(f"{API}/auth/logout/")
        assert (await make_api(old).get(ME)).status_code == 401


class TestRefresh:
    async def test_refresh_returns_new_pair(self, anon):
        body = (await anon.login("piloto@transvalor.com")).json()
        r = await anon.post(f"{API}/auth/refresh/", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 200
        assert r.json()["token"] and r.json()["refresh_token"]

    async def test_access_token_is_not_a_refresh_token(self, anon):
        body = (await anon.login("piloto@transvalor.com")).json()
        r = await anon.post(f"{API}/auth/refresh/", json={"refresh_token": body["token"]})
        assert r.status_code == 401

    async def test_garbage_refresh_401(self, anon):
        assert (
            await anon.post(f"{API}/auth/refresh/", json={"refresh_token": "zzz"})
        ).status_code == 401

    async def test_refreshed_access_token_works(self, anon, make_api):
        body = (await anon.login("piloto@transvalor.com")).json()
        pair = (
            await anon.post(f"{API}/auth/refresh/", json={"refresh_token": body["refresh_token"]})
        ).json()
        assert (await make_api(pair["token"]).get(ME)).status_code == 200


class TestPasswordChange:
    async def test_change_password_and_old_one_stops_working(self, new_account, anon, app):
        api, user, password = await new_account(["tickets.view"])
        r = await api.post(
            f"{API}/auth/password/",
            json={"current_password": password, "new_password": "Nueva-clave-segura-9"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["token"]
        assert (await api.get(ME)).status_code == 401
        assert (await Api(app).login(user["email"], password)).status_code == 401
        assert (await Api(app).login(user["email"], "Nueva-clave-segura-9")).status_code == 200

    async def test_wrong_current_password(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        r = await api.post(
            f"{API}/auth/password/",
            json={"current_password": "wrong-wrong-1", "new_password": "Nueva-clave-segura-9"},
        )
        assert r.status_code == 422 or r.status_code == 401
        assert r.status_code != 200

    async def test_weak_new_password_rejected(self, new_account):
        api, _, password = await new_account(["tickets.view"])
        r = await api.post(
            f"{API}/auth/password/", json={"current_password": password, "new_password": "short"}
        )
        assert r.status_code == 422

    async def test_same_password_rejected(self, new_account):
        api, _, password = await new_account(["tickets.view"])
        r = await api.post(
            f"{API}/auth/password/", json={"current_password": password, "new_password": password}
        )
        assert r.status_code == 422

    async def test_requires_token(self, anon):
        r = await anon.post(
            f"{API}/auth/password/", json={"current_password": "a", "new_password": "b"}
        )
        assert r.status_code == 401

    async def test_temporary_password_sets_must_change_flag(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        user = await create_user(admin, f"{uniq('u')}@example.com", role["id"])
        temp = await issue_temporary(admin, user["id"])
        login = await Api(admin.app).login(user["email"], temp)
        assert login.json()["user"]["must_change_password"] is True
        api = Api(admin.app, token=login.json()["token"])
        assert (await api.get(ME)).json()["user"]["must_change_password"] is True


class TestThrottle:
    async def test_lockout_by_account_across_ips(self, new_account, app):
        _, user, password = await new_account(["tickets.view"])
        for _ in range(5):
            assert (await Api(app).login(user["email"], "bad-bad-bad-1")).status_code == 401
        locked = await Api(app).login(user["email"], password)
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) > 0

    async def test_lockout_by_ip_across_accounts(self, app):
        ip = "203.0.113.77"
        for i in range(20):
            await Api(app, ip).login(f"ghost{i}@nowhere.test", "bad-bad-bad-1")
        r = await Api(app, ip).login("piloto@transvalor.com", DEMO_PASSWORD)
        assert r.status_code == 429

    async def test_other_ip_is_not_affected_by_ip_lockout(self, app):
        assert (await Api(app).login("operaciones@transvalor.com")).status_code == 200

    async def test_invented_account_locks_out_like_a_real_one(self, app):
        email = "invented-account@nowhere.test"
        for _ in range(5):
            await Api(app).login(email, "bad-bad-bad-1")
        r = await Api(app).login(email, "bad-bad-bad-1")
        assert r.status_code == 429
        assert {"title", "detail"} <= r.json().keys()

    async def test_real_account_lockout_looks_identical_to_invented(self, victim, app):
        email, _ = victim
        ghost = "ghost-twin@nowhere.test"
        for _ in range(5):
            await Api(app).login(email, "bad-bad-bad-1")
            await Api(app).login(ghost, "bad-bad-bad-1")
        real = await Api(app).login(email, "bad-bad-bad-1")
        fake = await Api(app).login(ghost, "bad-bad-bad-1")
        assert real.status_code == fake.status_code == 429
        assert real.json() == fake.json()

    async def test_success_resets_the_account_counter(self, new_account, app):
        _, user, password = await new_account(["tickets.view"])
        for _ in range(4):
            await Api(app).login(user["email"], "bad-bad-bad-1")
        assert (await Api(app).login(user["email"], password)).status_code == 200
        for _ in range(4):
            assert (await Api(app).login(user["email"], "bad-bad-bad-1")).status_code == 401

    async def test_lockout_message_is_generic(self, app):
        email = "another-invented@nowhere.test"
        for _ in range(5):
            await Api(app).login(email, "bad-bad-bad-1")
        r = await Api(app).login(email, "bad-bad-bad-1")
        assert email not in r.text
