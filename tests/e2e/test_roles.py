from tests.e2e.conftest import API, FULL_CATALOG, create_role, role_by_code, role_id

ZERO = "00000000-0000-0000-0000-000000000000"


class TestRoles:
    async def test_list_envelope(self, admin):
        r = await admin.get(f"{API}/roles/")
        body = r.json()
        assert r.status_code == 200
        assert {"data", "total", "page", "limit", "links"} <= body.keys()
        assert body["total"] >= 7
        assert {"next", "previous"} <= body["links"].keys()

    async def test_list_item_shape(self, admin):
        item = (await admin.get(f"{API}/roles/?is_system=true&limit=100")).json()["data"][0]
        assert {
            "id",
            "code",
            "name",
            "is_system",
            "is_active",
            "permissions",
            "users_count",
        } <= item.keys()

    async def test_pagination_limit_and_page(self, admin):
        first = (await admin.get(f"{API}/roles/?limit=2&page=1")).json()
        second = (await admin.get(f"{API}/roles/?limit=2&page=2")).json()
        assert len(first["data"]) == 2 and first["limit"] == 2
        assert {r["id"] for r in first["data"]}.isdisjoint({r["id"] for r in second["data"]})
        assert first["links"]["next"]

    async def test_limit_over_max_is_rejected(self, admin):
        assert (await admin.get(f"{API}/roles/?limit=100000")).status_code == 422

    async def test_search_q(self, admin):
        data = (await admin.get(f"{API}/roles/?q=piloto")).json()["data"]
        assert data and all(
            "pilot" in r["name"].lower() or "piloto" in r["name"].lower() for r in data
        )

    async def test_search_wildcards_are_literal(self, admin):
        data = (await admin.get(f"{API}/roles/?q=%25")).json()
        assert data["total"] == 0

    async def test_create_and_retrieve(self, admin, uniq):
        name = f"Rol {uniq()}"
        r = await admin.post(f"{API}/roles/", json={"name": name, "permissions": ["tickets.view"]})
        assert r.status_code == 201
        role = r.json()
        assert role["is_system"] is False and role["permissions"] == ["tickets.view"]
        again = (await admin.get(f"{API}/roles/{role['id']}/")).json()
        assert again["name"] == name

    async def test_create_ignores_forged_control_fields(self, admin, uniq):
        r = await admin.post(
            f"{API}/roles/",
            json={
                "name": f"Rol {uniq()}",
                "is_system": True,
                "has_all_permissions": True,
                "code": "superuser",
            },
        )
        role = r.json()
        assert r.status_code == 201
        assert (
            role["is_system"] is False and role["code"] != "superuser" and role["permissions"] == []
        )

    async def test_duplicate_name_422_case_insensitive(self, admin, uniq):
        name = f"Rol {uniq()}"
        await create_role(admin, name)
        r = await admin.post(f"{API}/roles/", json={"name": name.upper()})
        assert r.status_code == 422

    async def test_short_name_422(self, admin):
        assert (await admin.post(f"{API}/roles/", json={"name": "ab"})).status_code == 422

    async def test_update_name_and_permissions(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        r = await admin.patch(
            f"{API}/roles/{role['id']}/",
            json={"name": f"Renombrado {uniq()}", "permissions": ["tickets.view", "catalogs.view"]},
        )
        assert r.status_code == 200
        assert sorted(r.json()["permissions"]) == ["catalogs.view", "tickets.view"]

    async def test_update_can_remove_permissions(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view", "tickets.edit"])
        r = await admin.patch(f"{API}/roles/{role['id']}/", json={"permissions": ["tickets.view"]})
        assert r.json()["permissions"] == ["tickets.view"]

    async def test_superuser_permissions_are_immutable(self, admin):
        su = await role_by_code(admin, "superuser")
        r = await admin.patch(f"{API}/roles/{su['id']}/", json={"permissions": ["tickets.view"]})
        assert r.status_code == 422
        catalog = (await admin.get(f"{API}/permissions/")).json()
        assert FULL_CATALOG <= {p["code"] for p in catalog}
        assert len((await admin.get(f"{API}/roles/{su['id']}/")).json()["permissions"]) == len(
            catalog
        )

    async def test_superuser_cannot_be_deactivated_or_deleted(self, admin):
        su = await role_by_code(admin, "superuser")
        assert (
            await admin.patch(f"{API}/roles/{su['id']}/", json={"is_active": False})
        ).status_code == 422
        assert (await admin.delete(f"{API}/roles/{su['id']}/")).status_code == 422

    async def test_system_role_cannot_be_deleted(self, admin):
        pilot_role = await role_by_code(admin, "pilot")
        assert (await admin.delete(f"{API}/roles/{pilot_role['id']}/")).status_code == 422

    async def test_delete_unused_custom_role(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        assert (await admin.delete(f"{API}/roles/{role['id']}/")).status_code == 204
        assert (await admin.get(f"{API}/roles/{role['id']}/")).status_code == 404

    async def test_delete_role_with_users_blocked(self, admin, new_account):
        _, user, _ = await new_account(["tickets.view"])
        assert (await admin.delete(f"{API}/roles/{user['role_id']}/")).status_code == 422
        assert (await admin.get(f"{API}/roles/{user['role_id']}/")).status_code == 200

    async def test_users_count(self, admin, new_account):
        _, user, _ = await new_account(["tickets.view"])
        assert (await admin.get(f"{API}/roles/{user['role_id']}/")).json()["users_count"] == 1

    async def test_unknown_role_404(self, admin):
        assert (await admin.get(f"{API}/roles/{ZERO}/")).status_code == 404
        assert (await admin.patch(f"{API}/roles/{ZERO}/", json={"name": "abcd"})).status_code == 404
        assert (await admin.delete(f"{API}/roles/{ZERO}/")).status_code == 404

    async def test_malformed_id_422(self, admin):
        assert (await admin.get(f"{API}/roles/not-a-uuid/")).status_code == 422

    async def test_filter_whitelist_blocks_oracle_filters(self, admin):
        r = await admin.get(f"{API}/roles/?description__like=a&name__ilike=zzzz")
        assert r.status_code == 200
        assert r.json()["total"] >= 7

    async def test_order_by_is_ignored(self, admin):
        r = await admin.get(f"{API}/roles/?order_by=password_hash")
        assert r.status_code == 200

    async def test_role_changes_are_audited_with_diff(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        await admin.patch(f"{API}/roles/{role['id']}/", json={"description": "nueva descripción"})
        events = (await admin.get(f"{API}/audit/?entity=role&entity_id={role['id']}")).json()[
            "data"
        ]
        assert {e["action"] for e in events} >= {"create", "update"}
        update = next(e for e in events if e["action"] == "update")
        assert any(
            c["field"] == "description" and c["to"] == "nueva descripción"
            for c in update["changes"]
        )

    async def test_permission_change_is_audited(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}")
        await admin.patch(f"{API}/roles/{role['id']}/", json={"permissions": ["tickets.view"]})
        events = (
            await admin.get(
                f"{API}/audit/?entity=role&entity_id={role['id']}&action=permission_change"
            )
        ).json()["data"]
        assert events

    async def test_cannot_grant_permissions_not_held(self, new_account, admin, uniq):
        api, _, _ = await new_account(["roles.manage", "roles.view"])
        r = await api.post(
            f"{API}/roles/", json={"name": f"Rol {uniq()}", "permissions": ["audit.view"]}
        )
        assert r.status_code == 422

    async def test_can_grant_permissions_held(self, new_account, uniq):
        api, _, _ = await new_account(["roles.manage", "roles.view", "tickets.view"])
        r = await api.post(
            f"{API}/roles/", json={"name": f"Rol {uniq()}", "permissions": ["tickets.view"]}
        )
        assert r.status_code == 201

    async def test_pilot_forbidden_everywhere(self, pilot):
        assert (await pilot.get(f"{API}/roles/")).status_code == 403
        assert (await pilot.post(f"{API}/roles/", json={"name": "abcd"})).status_code == 403
        assert (await pilot.patch(f"{API}/roles/{ZERO}/", json={"name": "abcd"})).status_code == 403
        assert (await pilot.delete(f"{API}/roles/{ZERO}/")).status_code == 403

    async def test_roles_view_cannot_write(self, new_account, admin, uniq):
        api, _, _ = await new_account(["roles.view"])
        assert (await api.get(f"{API}/roles/")).status_code == 200
        assert (await api.post(f"{API}/roles/", json={"name": f"Rol {uniq()}"})).status_code == 403
