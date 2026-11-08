"""A user must not be able to raise their own privileges, by any route."""

from tests.e2e.conftest import API, Api, activate, create_role, create_user, role_by_code, role_id

ME = f"{API}/auth/me/"


class TestSelfEscalationViaProfile:
    async def test_patch_me_rejects_role_fields(self, pilot):
        for field, value in (
            ("role_id", "x"),
            ("role", "superuser"),
            ("is_admin", True),
            ("permissions", ["audit.view"]),
            ("granted_permissions", ["audit.view"]),
            ("is_active", True),
            ("email", "x@example.com"),
        ):
            r = await pilot.patch(ME, json={field: value})
            assert r.status_code == 422, field

    async def test_patch_me_changes_only_allowed_fields(self, new_account):
        api, user, _ = await new_account(["tickets.view"])
        r = await api.patch(ME, json={"full_name": "Nuevo Nombre", "phone": "123"})
        assert r.status_code == 200
        assert r.json()["user"]["full_name"] == "Nuevo Nombre"
        assert r.json()["user"]["role_code"] == user["role_code"]

    async def test_patch_me_with_signature_field_is_refused(self, pilot):
        assert (
            await pilot.patch(ME, json={"signature_url": "data:image/png;base64,AAAA"})
        ).status_code == 422

    async def test_permissions_unchanged_after_attempts(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        await api.patch(ME, json={"permissions": ["audit.view"]})
        assert (await api.get(ME)).json()["permissions"] == ["tickets.view"]
        assert (await api.get(f"{API}/audit/")).status_code == 403


class TestSelfEscalationViaUsersEndpoint:
    async def test_user_manager_cannot_change_own_role(self, new_account, admin):
        api, user, _ = await new_account(["users.manage", "users.view"])
        r = await api.patch(
            f"{API}/users/{user['id']}/", json={"role_id": await role_id(admin, "superuser")}
        )
        assert r.status_code == 422
        assert (await api.get(ME)).status_code == 200
        assert (await api.get(f"{API}/audit/")).status_code == 403

    async def test_user_manager_cannot_grant_self(self, new_account):
        api, user, _ = await new_account(["users.manage", "users.view"])
        r = await api.patch(
            f"{API}/users/{user['id']}/", json={"granted_permissions": ["audit.view"]}
        )
        assert r.status_code == 422

    async def test_user_manager_cannot_unrevoke_or_revoke_self(self, new_account):
        api, user, _ = await new_account(["users.manage", "users.view"])
        r = await api.patch(
            f"{API}/users/{user['id']}/", json={"revoked_permissions": ["users.view"]}
        )
        assert r.status_code == 422

    async def test_user_manager_cannot_deactivate_self(self, new_account):
        api, user, _ = await new_account(["users.manage"])
        assert (
            await api.patch(f"{API}/users/{user['id']}/", json={"is_active": False})
        ).status_code == 422

    async def test_can_still_edit_own_name_through_users(self, new_account):
        api, user, _ = await new_account(["users.manage", "users.view"])
        assert (
            await api.patch(f"{API}/users/{user['id']}/", json={"full_name": "Yo Mismo"})
        ).status_code == 200


class TestPrivilegeCeiling:
    async def test_cannot_create_user_with_superuser_role(self, new_account, uniq, admin):
        api, _, _ = await new_account(["users.manage", "users.view"])
        r = await api.post(
            f"{API}/users/",
            json={
                "full_name": "X Y",
                "email": f"{uniq()}@example.com",
                "role_id": await role_id(admin, "superuser"),
            },
        )
        assert r.status_code == 422

    async def test_cannot_assign_role_with_permissions_beyond_own(self, new_account, uniq, admin):
        api, _, _ = await new_account(["users.manage", "users.view"])
        rich = await create_role(admin, f"Rico {uniq()}", ["audit.view", "users.manage"])
        r = await api.post(
            f"{API}/users/",
            json={"full_name": "X Y", "email": f"{uniq()}@example.com", "role_id": rich["id"]},
        )
        assert r.status_code == 422

    async def test_can_assign_role_within_reach(self, new_account, uniq, admin):
        api, _, _ = await new_account(["users.manage", "users.view", "tickets.view"])
        poor = await create_role(admin, f"Pobre {uniq()}", ["tickets.view"])
        r = await api.post(
            f"{API}/users/",
            json={"full_name": "X Y", "email": f"{uniq()}@example.com", "role_id": poor["id"]},
        )
        assert r.status_code == 201

    async def test_cannot_grant_permission_not_held_to_someone_else(self, new_account, uniq, admin):
        api, _, _ = await new_account(["users.manage", "users.view", "tickets.view"])
        target = await create_user(admin, f"{uniq()}@example.com", await role_id(admin, "pilot"))
        r = await api.patch(
            f"{API}/users/{target['id']}/", json={"granted_permissions": ["audit.view"]}
        )
        assert r.status_code == 422

    async def test_two_accomplices_cannot_swap_into_admin_without_holding_it(
        self, new_account, admin
    ):
        a, _, _ = await new_account(["users.manage", "users.view"])
        _, b_user, _ = await new_account(["users.manage", "users.view"])
        r = await a.patch(
            f"{API}/users/{b_user['id']}/", json={"role_id": await role_id(admin, "superuser")}
        )
        assert r.status_code == 422

    async def test_signature_cannot_be_planted_by_an_admin(self, admin, new_account):
        _, user, _ = await new_account(["tickets.view"])
        r = await admin.patch(
            f"{API}/users/{user['id']}/",
            json={"signature_url": "data:image/png;base64,iVBORw0KGgo="},
        )
        assert r.status_code == 422
        assert (await admin.get(f"{API}/users/{user['id']}/")).json()["has_signature"] is False
