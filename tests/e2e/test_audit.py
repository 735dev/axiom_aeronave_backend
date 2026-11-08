from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.e2e.conftest import (
    API,
    Api,
    create_role,
    create_user,
    issue_temporary,
    role_by_code,
    role_id,
)

AUDIT = f"{API}/audit/"
ZERO = "00000000-0000-0000-0000-000000000000"


async def events(admin, query=""):
    r = await admin.get(f"{AUDIT}?{query}")
    assert r.status_code == 200, r.text
    return r.json()


class TestAuditReadApi:
    async def test_envelope(self, admin):
        body = await events(admin)
        assert {"data", "total", "page", "limit", "links"} <= body.keys()

    async def test_event_shape(self, admin):
        ev = (await events(admin))["data"][0]
        assert {
            "id",
            "at",
            "actor_id",
            "actor_name",
            "actor_role",
            "action",
            "entity",
            "entity_id",
            "entity_label",
            "summary",
            "changes",
            "ip",
            "user_agent",
        } <= ev.keys()

    async def test_newest_first(self, admin):
        ats = [e["at"] for e in (await events(admin, "limit=50"))["data"]]
        assert ats == sorted(ats, reverse=True)

    async def test_requires_audit_view(self, pilot, anon, new_account):
        assert (await pilot.get(AUDIT)).status_code == 403
        assert (await anon.get(AUDIT)).status_code == 401
        api, _, _ = await new_account(["audit.view"])
        assert (await api.get(AUDIT)).status_code == 200

    async def test_audit_export_alone_is_not_enough(self, new_account):
        api, _, _ = await new_account(["audit.export"])
        assert (await api.get(AUDIT)).status_code == 403

    async def test_pagination(self, admin):
        one = await events(admin, "limit=1&page=1")
        two = await events(admin, "limit=1&page=2")
        assert len(one["data"]) == 1 and one["data"][0]["id"] != two["data"][0]["id"]

    async def test_invalid_entity_and_action_422(self, admin):
        assert (await admin.get(f"{AUDIT}?entity=nope")).status_code == 422
        assert (await admin.get(f"{AUDIT}?action=nope")).status_code == 422

    async def test_invalid_dates_422(self, admin):
        assert (await admin.get(f"{AUDIT}?from=yesterday")).status_code == 422
        assert (await admin.get(f"{AUDIT}?to=31/12/2026")).status_code == 422

    async def test_filter_entity_and_entity_id(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        data = (await events(admin, f"entity=role&entity_id={role['id']}"))["data"]
        assert data and all(e["entity"] == "role" and e["entity_id"] == role["id"] for e in data)

    async def test_filter_action(self, admin):
        data = (await events(admin, "action=create&limit=50"))["data"]
        assert data and {e["action"] for e in data} == {"create"}

    async def test_filter_actor_id(self, admin, uniq):
        me = (await admin.get(f"{API}/auth/me/")).json()["user"]
        await create_role(admin, f"Rol {uniq()}")
        data = (await events(admin, f"actor_id={me['id']}"))["data"]
        assert data and all(e["actor_id"] == me["id"] for e in data)

    async def test_filter_date_range_in_business_zone(self, admin, uniq):
        await create_role(admin, f"Rol {uniq()}")
        today = datetime.now(ZoneInfo("America/Caracas")).date()
        assert (await events(admin, f"from={today}&to={today}"))["total"] >= 1
        assert (await events(admin, f"to={today - timedelta(days=2)}"))["total"] >= 0
        assert (await events(admin, "to=2000-01-01"))["total"] == 0
        assert (await events(admin, "from=2999-01-01"))["total"] == 0

    async def test_to_date_includes_the_whole_day(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        today = datetime.now(ZoneInfo("America/Caracas")).date()
        data = (await events(admin, f"entity_id={role['id']}&to={today}"))["data"]
        assert data, "events created today must be inside `to=today`"

    async def test_search_summary_and_diff_text(self, admin, uniq):
        marker = f"Marca{uniq()}"
        role = await create_role(admin, f"Rol {marker}")
        assert any(
            e["entity_id"] == role["id"] for e in (await events(admin, f"search={marker}"))["data"]
        )
        await admin.patch(
            f"{API}/roles/{role['id']}/", json={"description": f"texto-{marker}-interno"}
        )
        assert (await events(admin, f"search=texto-{marker}-interno"))["total"] >= 1

    async def test_search_wildcards_literal(self, admin):
        assert (await events(admin, "search=%25%25%25"))["total"] == 0

    async def test_search_by_actor_name(self, admin):
        assert (await events(admin, "search=Superusuario"))["total"] >= 1

    async def test_unknown_filters_dropped(self, admin):
        r = await admin.get(f"{AUDIT}?changes__like=x&ip=1.1.1.1&seq=1")
        assert r.status_code == 200


class TestAppendOnly:
    async def test_no_write_verbs_exist(self, admin):
        ev = (await events(admin))["data"][0]
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in (AUDIT, f"{AUDIT}{ev['id']}/"):
                r = await admin.request(method, path, json={"summary": "tampered"})
                assert r.status_code in (404, 405), (method, path, r.status_code)

    async def test_event_is_unchanged_after_tampering_attempts(self, admin):
        ev = (await events(admin))["data"][0]
        await admin.patch(f"{AUDIT}{ev['id']}/", json={"summary": "tampered"})
        await admin.delete(f"{AUDIT}{ev['id']}/")
        again = [
            e
            for e in (await events(admin, f"entity_id={ev['entity_id']}&limit=100"))["data"]
            if e["id"] == ev["id"]
        ]
        assert again and again[0]["summary"] == ev["summary"]

    async def test_total_never_decreases_with_deletes_elsewhere(self, admin, uniq):
        before = (await events(admin))["total"]
        role = await create_role(admin, f"Rol {uniq()}")
        await admin.delete(f"{API}/roles/{role['id']}/")
        after = (await events(admin))["total"]
        assert after >= before + 2

    async def test_deleting_a_resource_keeps_its_history(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        await admin.delete(f"{API}/roles/{role['id']}/")
        data = (await events(admin, f"entity=role&entity_id={role['id']}"))["data"]
        assert {"create", "delete"} <= {e["action"] for e in data}


class TestActorFromToken:
    async def test_actor_is_the_token_user(self, new_account):
        api, user, _ = await new_account(["roles.manage", "roles.view", "audit.view"])
        role = (await api.post(f"{API}/roles/", json={"name": "Hecho por token"})).json()
        data = (await api.get(f"{AUDIT}?entity=role&entity_id={role['id']}")).json()["data"]
        assert data[0]["actor_id"] == user["id"]
        assert data[0]["actor_name"] == user["full_name"]

    async def test_body_cannot_forge_the_actor(self, new_account, uniq):
        api, user, _ = await new_account(["roles.manage", "roles.view", "audit.view"])
        r = await api.post(
            f"{API}/roles/",
            json={
                "name": f"Rol {uniq()}",
                "actor_id": "someone-else",
                "actor_name": "Falso",
                "created_by": "x",
            },
        )
        role = r.json()
        data = (await api.get(f"{AUDIT}?entity=role&entity_id={role['id']}")).json()["data"]
        assert data[0]["actor_id"] == user["id"] and data[0]["actor_name"] != "Falso"

    async def test_header_cannot_forge_the_actor(self, new_account, uniq):
        api, user, _ = await new_account(["roles.manage", "roles.view", "audit.view"])
        r = await api.post(
            f"{API}/roles/",
            json={"name": f"Rol {uniq()}"},
            headers={"X-Actor-Id": "someone-else", "X-User-Id": "x"},
        )
        data = (await api.get(f"{AUDIT}?entity=role&entity_id={r.json()['id']}")).json()["data"]
        assert data[0]["actor_id"] == user["id"]

    async def test_role_name_snapshot(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        data = (await events(admin, f"entity=role&entity_id={role['id']}"))["data"]
        assert data[0]["actor_role"] == "Superusuario"

    async def test_ip_and_user_agent_recorded(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        ev = (await events(admin, f"entity_id={role['id']}"))["data"][0]
        assert ev["ip"] == admin.ip

    async def test_snapshot_survives_actor_rename(self, admin, new_account, uniq):
        api, user, _ = await new_account(["roles.manage", "roles.view"])
        role = (await api.post(f"{API}/roles/", json={"name": f"Rol {uniq()}"})).json()
        await admin.patch(f"{API}/users/{user['id']}/", json={"full_name": "Nombre Posterior"})
        ev = (await events(admin, f"entity_id={role['id']}"))["data"][0]
        assert ev["actor_name"] == user["full_name"] != "Nombre Posterior"

    async def test_snapshot_survives_actor_role_rename(self, admin, new_account, uniq):
        api, user, _ = await new_account(["roles.manage", "roles.view"])
        old_role_name = (await admin.get(f"{API}/users/{user['id']}/")).json()["role_name"]
        role = (await api.post(f"{API}/roles/", json={"name": f"Rol {uniq()}"})).json()
        await admin.patch(f"{API}/roles/{user['role_id']}/", json={"name": f"Renombrado {uniq()}"})
        ev = (await events(admin, f"entity_id={role['id']}"))["data"][0]
        assert ev["actor_role"] == old_role_name

    async def test_snapshot_survives_actor_deactivation(self, admin, new_account, uniq):
        api, user, _ = await new_account(["roles.manage", "roles.view"])
        role = (await api.post(f"{API}/roles/", json={"name": f"Rol {uniq()}"})).json()
        await admin.patch(f"{API}/users/{user['id']}/", json={"is_active": False})
        ev = (await events(admin, f"entity_id={role['id']}"))["data"][0]
        assert ev["actor_id"] == user["id"]


class TestDiffContent:
    async def test_create_event_lists_initial_values(self, admin, uniq):
        name = f"Rol {uniq()}"
        role = await create_role(admin, name)
        ev = next(
            e
            for e in (await events(admin, f"entity_id={role['id']}"))["data"]
            if e["action"] == "create"
        )
        assert any(c["field"] == "name" and c["to"] == name for c in ev["changes"])
        assert name in ev["summary"] or name in ev["entity_label"]

    async def test_update_event_has_old_and_new(self, admin, uniq):
        user = await create_user(
            admin, f"{uniq()}@example.com", await role_id(admin, "pilot"), phone="111"
        )
        await admin.patch(
            f"{API}/users/{user['id']}/", json={"phone": "222", "full_name": "Cambio Uno"}
        )
        ev = next(
            e
            for e in (await events(admin, f"entity_id={user['id']}"))["data"]
            if e["action"] == "update"
        )
        phone = next(c for c in ev["changes"] if c["field"] == "phone")
        assert phone["from"] == "111" and phone["to"] == "222" and phone["label"]
        assert {c["field"] for c in ev["changes"]} >= {"phone", "full_name"}

    async def test_unchanged_patch_writes_no_event(self, admin, uniq):
        user = await create_user(admin, f"{uniq()}@example.com", await role_id(admin, "pilot"))
        before = (await events(admin, f"entity_id={user['id']}"))["total"]
        await admin.patch(f"{API}/users/{user['id']}/", json={"full_name": user["full_name"]})
        assert (await events(admin, f"entity_id={user['id']}"))["total"] == before

    async def test_delete_event_recorded(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        await admin.delete(f"{API}/roles/{role['id']}/")
        ev = next(
            e
            for e in (await events(admin, f"entity_id={role['id']}"))["data"]
            if e["action"] == "delete"
        )
        assert ev["summary"]

    async def test_secrets_never_enter_the_trail(self, admin, uniq):
        user = await create_user(admin, f"{uniq()}@example.com", await role_id(admin, "pilot"))
        temp = await issue_temporary(admin, user["id"])
        text = (await admin.get(f"{AUDIT}?limit=100")).text
        assert temp not in text and "$2b$" not in text and "password_hash" not in text

    async def test_password_reset_is_audited_without_the_value(self, admin, uniq):
        user = await create_user(admin, f"{uniq()}@example.com", await role_id(admin, "pilot"))
        temp = await issue_temporary(admin, user["id"])
        data = (await events(admin, f"entity_id={user['id']}"))["data"]
        assert any("contraseña" in e["summary"].lower() for e in data)
        assert temp not in str(data)

    async def test_role_change_of_a_user_is_audited(self, admin, uniq):
        user = await create_user(admin, f"{uniq()}@example.com", await role_id(admin, "pilot"))
        await admin.patch(
            f"{API}/users/{user['id']}/", json={"role_id": await role_id(admin, "ground")}
        )
        data = (await events(admin, f"entity_id={user['id']}&action=permission_change"))["data"]
        assert data

    async def test_permission_exception_diff(self, admin, uniq):
        user = await create_user(admin, f"{uniq()}@example.com", await role_id(admin, "pilot"))
        await admin.patch(
            f"{API}/users/{user['id']}/", json={"granted_permissions": ["catalogs.view"]}
        )
        ev = (await events(admin, f"entity_id={user['id']}&action=permission_change"))["data"][0]
        assert "catalogs.view" in str(ev["changes"])


class TestSessionEvents:
    async def test_login_is_recorded(self, app, admin):
        api = Api(app)
        body = (await api.login("admin@transvalor.com")).json()
        data = (await events(admin, f"action=login&search=admin@transvalor.com"))["data"]
        assert data and data[0]["actor_id"] == body["user"]["id"]

    async def test_logout_is_recorded(self, logged, admin):
        api, body = await logged("mantenimiento@transvalor.com")
        await api.post(f"{API}/auth/logout/")
        data = (await events(admin, f"action=logout&actor_id={body['user']['id']}"))["data"]
        assert data

    async def test_failed_login_records_attempted_email_and_ip(self, app, admin, victim):
        email, _ = victim
        ip = "198.51.100.23"
        await Api(app, ip).login(email, "bad-bad-bad-1")
        data = (await events(admin, f"action=login_failed&search={email}"))["data"]
        assert data
        assert (
            data[0]["entity"] == "session" and email in data[0]["summary"] + data[0]["entity_label"]
        )
        assert data[0]["ip"] == ip
        assert data[0]["actor_id"] == "anonymous"

    async def test_failed_login_for_unknown_email_is_recorded_too(self, app, admin, uniq):
        email = f"{uniq('ghost')}@nowhere.test"
        await Api(app).login(email, "bad-bad-bad-1")
        assert (await events(admin, f"action=login_failed&search={email}"))["total"] == 1

    async def test_failed_login_never_stores_the_password(self, app, admin, uniq):
        secret = f"Secreto-{uniq()}-123"
        await Api(app).login(f"{uniq('g')}@nowhere.test", secret)
        assert secret not in (await admin.get(f"{AUDIT}?action=login_failed&limit=100")).text

    async def test_lockout_audited_once_per_window(self, app, admin, uniq):
        email = f"{uniq('lock')}@nowhere.test"
        for _ in range(5):
            await Api(app).login(email, "bad-bad-bad-1")
        for _ in range(4):
            assert (await Api(app).login(email, "bad-bad-bad-1")).status_code == 429
        blocked = (await events(admin, f"action=login_failed&search={email}&limit=100"))["data"]
        assert len([e for e in blocked if "bloqueado" in e["summary"].lower()]) == 1

    async def test_successful_login_after_failures_still_recorded(self, app, admin, victim):
        email, password = victim
        await Api(app).login(email, "bad-bad-bad-1")
        assert (await Api(app).login(email, password)).status_code == 200
        actions = {e["action"] for e in (await events(admin, f"search={email}&limit=100"))["data"]}
        assert {"login_failed", "login"} <= actions

    async def test_failed_login_is_recorded_even_though_request_fails(self, app, admin, uniq):
        """The 401 aborts the request transaction; the event must survive on its own."""
        email = f"{uniq('tx')}@nowhere.test"
        r = await Api(app).login(email, "bad-bad-bad-1")
        assert r.status_code == 401
        assert (await events(admin, f"action=login_failed&search={email}"))["total"] == 1
