from tests.e2e.conftest import (
    API,
    Api,
    activate,
    create_role,
    create_user,
    issue_temporary,
    role_by_code,
    role_id,
)

ZERO = "00000000-0000-0000-0000-000000000000"


class TestUsers:
    async def test_list_envelope_and_no_secret_fields(self, admin):
        r = await admin.get(f"{API}/users/")
        body = r.json()
        assert r.status_code == 200 and body["total"] >= 6
        text = r.text
        for leaked in ("password_hash", "tokens_valid_after", "signature_url"):
            assert leaked not in text

    async def test_item_shape(self, admin):
        item = (await admin.get(f"{API}/users/?limit=100")).json()["data"][0]
        assert {
            "id",
            "full_name",
            "email",
            "role_id",
            "role_code",
            "role_name",
            "is_active",
            "granted_permissions",
            "revoked_permissions",
            "has_signature",
            "created_at",
        } <= item.keys()

    async def test_search_by_name_and_email(self, admin):
        assert (await admin.get(f"{API}/users/?q=rodr")).json()["total"] >= 1
        assert (await admin.get(f"{API}/users/?q=tierra@")).json()["total"] == 1

    async def test_filter_by_role_and_active(self, admin):
        rid = await role_id(admin, "pilot")
        data = (await admin.get(f"{API}/users/?role_id={rid}&is_active=true")).json()["data"]
        assert data and all(u["role_id"] == rid for u in data)

    async def test_hidden_columns_cannot_be_probed_as_filters(self, admin):
        base = (await admin.get(f"{API}/users/")).json()["total"]
        for probe in (
            "password_hash__like=$2b%25",
            "password_hash=x",
            "signature_url__ilike=data",
            "tokens_valid_after__gte=2000-01-01",
        ):
            r = await admin.get(f"{API}/users/?{probe}")
            assert r.status_code == 200 and r.json()["total"] == base, probe

    async def test_order_by_hidden_column_ignored(self, admin):
        assert (await admin.get(f"{API}/users/?order_by=password_hash")).status_code == 200

    async def test_create_returns_user_without_password(self, admin, uniq):
        pid = await role_id(admin, "pilot")
        r = await admin.post(
            f"{API}/users/",
            json={
                "full_name": "Nuevo Piloto",
                "email": f"{uniq('n')}@example.com",
                "role_id": pid,
                "location": "charallave",
                "aircraft_id": "YV2323",
            },
        )
        assert r.status_code == 201
        user = r.json()
        assert user["is_active"] is True and user["must_change_password"] is True
        assert "password" not in r.text.replace("must_change_password", "")

    async def test_new_user_cannot_login_until_reset(self, admin, uniq, app):
        email = f"{uniq('n')}@example.com"
        user = await create_user(admin, email, await role_id(admin, "pilot"))
        assert (await Api(app).login(email, "demo1234")).status_code == 401
        temp = await issue_temporary(admin, user["id"])
        assert (await Api(app).login(email, temp)).status_code == 200

    async def test_temporary_password_is_random_and_strong(self, admin, uniq):
        user = await create_user(admin, f"{uniq('n')}@example.com", await role_id(admin, "pilot"))
        a, b = await issue_temporary(admin, user["id"]), await issue_temporary(admin, user["id"])
        assert a != b and len(a) >= 12

    async def test_email_normalised_and_unique(self, admin, uniq):
        mail = f"{uniq('N')}@Example.com"
        pid = await role_id(admin, "pilot")
        user = await create_user(admin, mail, pid)
        assert user["email"] == mail.lower()
        r = await admin.post(
            f"{API}/users/", json={"full_name": "Otro", "email": mail.upper(), "role_id": pid}
        )
        assert r.status_code == 422

    async def test_create_rejects_forged_control_fields(self, admin, uniq):
        pid = await role_id(admin, "pilot")
        payload = {
            "full_name": "X Y",
            "email": f"{uniq('f')}@example.com",
            "role_id": pid,
            "is_admin": True,
            "is_active": False,
            "password": "hacked-12345",
            "must_change_password": False,
            "tokens_valid_after": "2000-01-01T00:00:00Z",
        }
        r = await admin.post(f"{API}/users/", json=payload)
        assert r.status_code == 201
        user = r.json()
        assert user["is_active"] is True and user["must_change_password"] is True
        assert (await Api(admin.app).login(payload["email"], "hacked-12345")).status_code == 401

    async def test_create_validates_role_and_catalog_codes(self, admin, uniq):
        pid = await role_id(admin, "pilot")
        base = {"full_name": "X Y", "email": f"{uniq('v')}@example.com"}
        assert (
            await admin.post(f"{API}/users/", json={**base, "role_id": ZERO})
        ).status_code == 422
        r = await admin.post(f"{API}/users/", json={**base, "role_id": pid, "aircraft_id": "NOPE"})
        assert r.status_code == 422
        r = await admin.post(f"{API}/users/", json={**base, "role_id": pid, "location": "NOPE"})
        assert r.status_code == 422

    async def test_create_invalid_email_422(self, admin):
        pid = await role_id(admin, "pilot")
        r = await admin.post(
            f"{API}/users/", json={"full_name": "X", "email": "nope", "role_id": pid}
        )
        assert r.status_code == 422 and "email" in r.json()["errors"]

    async def test_inactive_role_cannot_be_assigned(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        await admin.patch(f"{API}/roles/{role['id']}/", json={"is_active": False})
        r = await admin.post(
            f"{API}/users/",
            json={"full_name": "X Y", "email": f"{uniq('i')}@example.com", "role_id": role["id"]},
        )
        assert r.status_code == 422

    async def test_update_profile_fields(self, admin, uniq):
        user = await create_user(admin, f"{uniq('u')}@example.com", await role_id(admin, "pilot"))
        r = await admin.patch(
            f"{API}/users/{user['id']}/",
            json={"full_name": "Cambiado", "phone": "+58 412 0000000", "location": "caracas"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["full_name"] == "Cambiado" and body["location"] == "caracas"

    async def test_empty_string_clears_optional_field(self, admin, uniq):
        user = await create_user(
            admin, f"{uniq('u')}@example.com", await role_id(admin, "pilot"), phone="123"
        )
        r = await admin.patch(f"{API}/users/{user['id']}/", json={"phone": ""})
        assert r.json()["phone"] is None

    async def test_email_is_immutable(self, admin, uniq):
        user = await create_user(admin, f"{uniq('u')}@example.com", await role_id(admin, "pilot"))
        r = await admin.patch(f"{API}/users/{user['id']}/", json={"email": "other@example.com"})
        assert r.status_code in (200, 422)
        assert (await admin.get(f"{API}/users/{user['id']}/")).json()["email"] == user["email"]

    async def test_deactivate_and_reactivate(self, admin, uniq):
        user = await create_user(admin, f"{uniq('u')}@example.com", await role_id(admin, "pilot"))
        assert (await admin.patch(f"{API}/users/{user['id']}/", json={"is_active": False})).json()[
            "is_active"
        ] is False
        assert (await admin.patch(f"{API}/users/{user['id']}/", json={"is_active": True})).json()[
            "is_active"
        ] is True

    async def test_no_delete_endpoint(self, admin, uniq):
        user = await create_user(admin, f"{uniq('u')}@example.com", await role_id(admin, "pilot"))
        assert (await admin.delete(f"{API}/users/{user['id']}/")).status_code in (404, 405)

    async def test_unknown_user_404_on_every_verb(self, admin):
        assert (await admin.get(f"{API}/users/{ZERO}/")).status_code == 404
        assert (
            await admin.patch(f"{API}/users/{ZERO}/", json={"full_name": "x"})
        ).status_code == 404
        assert (await admin.get(f"{API}/users/{ZERO}/effective-permissions/")).status_code == 404
        assert (await admin.post(f"{API}/users/{ZERO}/reset-password/")).status_code == 404

    async def test_404_does_not_echo_the_id(self, admin):
        r = await admin.get(f"{API}/users/{ZERO}/")
        assert ZERO not in r.text

    async def test_cannot_reset_own_password_via_admin_endpoint(self, admin):
        me = (await admin.get(f"{API}/auth/me/")).json()["user"]
        assert (await admin.post(f"{API}/users/{me['id']}/reset-password/")).status_code == 422

    async def test_last_superuser_cannot_be_deactivated_or_demoted(self, admin, uniq):
        sid = await role_id(admin, "superuser")
        # a second superuser performs the actions on the first one
        second = await create_user(admin, f"{uniq('s')}@example.com", sid)
        temp = await activate(admin, second["id"])
        r = await Api(admin.app).login(second["email"], temp)
        assert r.status_code == 200
        second_api = Api(admin.app, token=r.json()["token"])
        users = (await admin.get(f"{API}/users/?role_id={sid}&limit=100")).json()["data"]
        # deactivate everyone but `second`, via `second`
        for u in users:
            if u["id"] != second["id"] and u["is_active"]:
                assert (
                    await second_api.patch(f"{API}/users/{u['id']}/", json={"is_active": False})
                ).status_code == 200
        # the last active superuser is `second`: a different admin cannot exist, so ask the guard via self-change
        me = (await second_api.get(f"{API}/auth/me/")).json()["user"]
        assert (
            await second_api.patch(f"{API}/users/{me['id']}/", json={"is_active": False})
        ).status_code == 422
        # restore the seeded superuser so other tests keep working
        original = next(u for u in users if u["email"] == "super@transvalor.com")
        assert (
            await second_api.patch(f"{API}/users/{original['id']}/", json={"is_active": True})
        ).status_code == 200

    async def test_roles_manage_only_cannot_manage_users(self, new_account):
        api, _, _ = await new_account(["roles.manage", "roles.view"])
        assert (await api.get(f"{API}/users/")).status_code == 403

    async def test_users_view_cannot_write(self, new_account, admin, uniq):
        api, _, _ = await new_account(["users.view"])
        pid = await role_id(admin, "pilot")
        assert (await api.get(f"{API}/users/")).status_code == 200
        assert (
            await api.post(
                f"{API}/users/",
                json={"full_name": "X Y", "email": f"{uniq()}@example.com", "role_id": pid},
            )
        ).status_code == 403
        assert (await api.patch(f"{API}/users/{ZERO}/", json={"full_name": "x"})).status_code == 403
        assert (await api.post(f"{API}/users/{ZERO}/reset-password/")).status_code == 403

    async def test_user_changes_are_audited(self, admin, uniq):
        user = await create_user(admin, f"{uniq('u')}@example.com", await role_id(admin, "pilot"))
        await admin.patch(f"{API}/users/{user['id']}/", json={"full_name": "Nombre Nuevo"})
        events = (await admin.get(f"{API}/audit/?entity=user&entity_id={user['id']}")).json()[
            "data"
        ]
        assert {"create", "update"} <= {e["action"] for e in events}
