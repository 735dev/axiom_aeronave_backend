from tests.e2e.conftest import API, FULL_CATALOG, Api, activate, create_role, create_user

ME = f"{API}/auth/me/"


async def effective(admin, user_id):
    r = await admin.get(f"{API}/users/{user_id}/effective-permissions/")
    assert r.status_code == 200, r.text
    return {p["code"]: p["origin"] for p in r.json()}


async def make_user(admin, uniq, perms, **extra):
    role = await create_role(admin, f"Rol {uniq()}", perms)
    return role, await create_user(admin, f"{uniq('e')}@example.com", role["id"], **extra)


class TestEffectivePermissionsMath:
    async def test_role_only(self, admin, uniq):
        _, user = await make_user(admin, uniq, ["tickets.view", "tickets.create"])
        assert await effective(admin, user["id"]) == {
            "tickets.view": "role",
            "tickets.create": "role",
        }

    async def test_grant_adds(self, admin, uniq):
        _, user = await make_user(
            admin, uniq, ["tickets.view"], granted_permissions=["catalogs.view"]
        )
        assert await effective(admin, user["id"]) == {
            "tickets.view": "role",
            "catalogs.view": "granted",
        }

    async def test_revoke_removes(self, admin, uniq):
        _, user = await make_user(
            admin, uniq, ["tickets.view", "tickets.create"], revoked_permissions=["tickets.create"]
        )
        eff = await effective(admin, user["id"])
        assert "tickets.create" not in eff or eff["tickets.create"] == "revoked"
        assert eff["tickets.view"] == "role"

    async def test_role_plus_grant_minus_revoke(self, admin, uniq):
        _, user = await make_user(
            admin,
            uniq,
            ["tickets.view", "tickets.edit"],
            granted_permissions=["catalogs.view", "analytics.view"],
            revoked_permissions=["tickets.edit"],
        )
        api_codes = None
        pw = await activate(admin, user["id"])
        r = await Api(admin.app).login(user["email"], pw)
        api_codes = set(r.json()["permissions"])
        assert api_codes == {"tickets.view", "catalogs.view", "analytics.view"}

    async def test_login_and_me_agree_on_permissions(self, admin, uniq):
        _, user = await make_user(
            admin, uniq, ["tickets.view"], granted_permissions=["catalogs.view"]
        )
        pw = await activate(admin, user["id"])
        login = (await Api(admin.app).login(user["email"], pw)).json()
        me = (await Api(admin.app, token=login["token"]).get(ME)).json()
        assert (
            sorted(login["permissions"])
            == sorted(me["permissions"])
            == ["catalogs.view", "tickets.view"]
        )

    async def test_grant_unlocks_an_endpoint_and_revoke_closes_it(self, admin, uniq):
        _, user = await make_user(admin, uniq, ["tickets.view", "users.view"])
        pw = await activate(admin, user["id"])
        api = Api(admin.app, token=(await Api(admin.app).login(user["email"], pw)).json()["token"])
        assert (await api.get(f"{API}/users/")).status_code == 200
        assert (await api.get(f"{API}/audit/")).status_code == 403
        await admin.patch(
            f"{API}/users/{user['id']}/",
            json={"granted_permissions": ["audit.view"], "revoked_permissions": ["users.view"]},
        )
        pw = await activate(admin, user["id"])
        api = Api(admin.app, token=(await Api(admin.app).login(user["email"], pw)).json()["token"])
        assert (await api.get(f"{API}/audit/")).status_code == 200
        assert (await api.get(f"{API}/users/")).status_code == 403

    async def test_permission_change_applies_to_live_token_immediately(self, admin, uniq):
        """Permissions are read per request, not baked into the token."""
        _, user = await make_user(admin, uniq, ["tickets.view"])
        pw = await activate(admin, user["id"])
        api = Api(admin.app, token=(await Api(admin.app).login(user["email"], pw)).json()["token"])
        assert (await api.get(f"{API}/audit/")).status_code == 403
        role = (await admin.get(f"{API}/users/{user['id']}/")).json()["role_id"]
        await admin.patch(
            f"{API}/roles/{role}/", json={"permissions": ["tickets.view", "audit.view"]}
        )
        assert (await api.get(f"{API}/audit/")).status_code == 200

    async def test_deactivated_role_stops_access(self, admin, uniq):
        role, user = await make_user(admin, uniq, ["tickets.view"])
        pw = await activate(admin, user["id"])
        api = Api(admin.app, token=(await Api(admin.app).login(user["email"], pw)).json()["token"])
        await admin.patch(f"{API}/roles/{role['id']}/", json={"is_active": False})
        assert (await api.get(ME)).status_code == 401

    async def test_grant_already_in_role_422(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        r = await admin.post(
            f"{API}/users/",
            json={
                "full_name": "X Y",
                "email": f"{uniq()}@example.com",
                "role_id": role["id"],
                "granted_permissions": ["tickets.view"],
            },
        )
        assert r.status_code == 422

    async def test_revoke_something_role_lacks_422(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        r = await admin.post(
            f"{API}/users/",
            json={
                "full_name": "X Y",
                "email": f"{uniq()}@example.com",
                "role_id": role["id"],
                "revoked_permissions": ["audit.view"],
            },
        )
        assert r.status_code == 422

    async def test_same_permission_granted_and_revoked_422(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        r = await admin.post(
            f"{API}/users/",
            json={
                "full_name": "X Y",
                "email": f"{uniq()}@example.com",
                "role_id": role["id"],
                "granted_permissions": ["audit.view"],
                "revoked_permissions": ["audit.view"],
            },
        )
        assert r.status_code == 422

    async def test_unknown_permission_code_422(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        r = await admin.post(
            f"{API}/users/",
            json={
                "full_name": "X Y",
                "email": f"{uniq()}@example.com",
                "role_id": role["id"],
                "granted_permissions": ["made.up"],
            },
        )
        assert r.status_code == 422

    async def test_role_change_clears_exceptions(self, admin, uniq):
        role, user = await make_user(
            admin, uniq, ["tickets.view"], granted_permissions=["catalogs.view"]
        )
        other = await create_role(admin, f"Otro {uniq()}", ["tickets.edit"])
        r = await admin.patch(f"{API}/users/{user['id']}/", json={"role_id": other["id"]})
        assert r.status_code == 200
        assert r.json()["granted_permissions"] == []
        assert await effective(admin, user["id"]) == {"tickets.edit": "role"}

    async def test_role_change_with_new_exceptions(self, admin, uniq):
        _, user = await make_user(admin, uniq, ["tickets.view"])
        other = await create_role(admin, f"Otro {uniq()}", ["tickets.edit"])
        r = await admin.patch(
            f"{API}/users/{user['id']}/",
            json={"role_id": other["id"], "granted_permissions": ["catalogs.view"]},
        )
        assert r.status_code == 200
        assert r.json()["granted_permissions"] == ["catalogs.view"]

    async def test_clearing_exceptions_with_empty_lists(self, admin, uniq):
        _, user = await make_user(
            admin, uniq, ["tickets.view"], granted_permissions=["catalogs.view"]
        )
        r = await admin.patch(f"{API}/users/{user['id']}/", json={"granted_permissions": []})
        assert r.json()["granted_permissions"] == []

    async def test_superuser_effective_is_whole_catalog(self, admin):
        me = (await admin.get(ME)).json()["user"]
        catalog = {p["code"] for p in (await admin.get(f"{API}/permissions/")).json()}
        assert FULL_CATALOG <= catalog
        assert len(await effective(admin, me["id"])) == len(catalog)

    async def test_new_catalog_entries_would_flow_to_superuser(self, admin):
        perms = {p["code"] for p in (await admin.get(f"{API}/permissions/")).json()}
        me = (await admin.get(ME)).json()
        assert set(me["permissions"]) == perms

    async def test_effective_endpoint_needs_users_view(self, pilot, admin):
        me = (await admin.get(ME)).json()["user"]
        assert (
            await pilot.get(f"{API}/users/{me['id']}/effective-permissions/")
        ).status_code == 403

    async def test_exception_change_is_audited_as_permission_change(self, admin, uniq):
        _, user = await make_user(admin, uniq, ["tickets.view"])
        await admin.patch(
            f"{API}/users/{user['id']}/", json={"granted_permissions": ["catalogs.view"]}
        )
        events = (
            await admin.get(
                f"{API}/audit/?entity=user&entity_id={user['id']}&action=permission_change"
            )
        ).json()["data"]
        assert events
