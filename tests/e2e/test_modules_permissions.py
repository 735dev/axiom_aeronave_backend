from tests.e2e.conftest import API, FULL_CATALOG

ZERO = "00000000-0000-0000-0000-000000000000"


class TestModules:
    async def test_list_is_a_plain_array_with_seeded_modules(self, pilot):
        r = await pilot.get(f"{API}/modules/")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list) and len(body) == 12
        assert {"id", "code", "name", "show_in_menu", "sort_order", "permissions_count"} <= body[
            0
        ].keys()

    async def test_list_sorted_by_sort_order(self, pilot):
        orders = [m["sort_order"] for m in (await pilot.get(f"{API}/modules/")).json()]
        assert orders == sorted(orders)

    async def test_permissions_count_matches_catalog(self, admin):
        modules = (await admin.get(f"{API}/modules/")).json()
        catalog = (await admin.get(f"{API}/permissions/")).json()
        assert FULL_CATALOG <= {p["code"] for p in catalog}
        assert sum(m["permissions_count"] for m in modules) == len(catalog)

    async def test_requires_authentication(self, anon):
        assert (await anon.get(f"{API}/modules/")).status_code == 401

    async def test_patch_needs_roles_manage(self, pilot, admin):
        mod = (await admin.get(f"{API}/modules/")).json()[0]
        assert (
            await pilot.patch(f"{API}/modules/{mod['id']}/", json={"name": "Hack"})
        ).status_code == 403

    async def test_patch_updates_menu_entry(self, admin):
        mod = (await admin.get(f"{API}/modules/")).json()[-1]
        r = await admin.patch(f"{API}/modules/{mod['id']}/", json={"sort_order": mod["sort_order"]})
        assert r.status_code == 200 and r.json()["code"] == mod["code"]

    async def test_patch_cannot_change_code_or_system_flag(self, admin):
        mod = (await admin.get(f"{API}/modules/")).json()[0]
        r = await admin.patch(
            f"{API}/modules/{mod['id']}/", json={"code": "hacked", "is_system": False}
        )
        assert r.status_code in (200, 422)
        after = (await admin.get(f"{API}/modules/")).json()
        assert [m["code"] for m in after if m["id"] == mod["id"]] == [mod["code"]]
        assert [m["is_system"] for m in after if m["id"] == mod["id"]] == [mod["is_system"]]

    async def test_patch_unknown_menu_permission_422(self, admin):
        mod = (await admin.get(f"{API}/modules/")).json()[0]
        r = await admin.patch(f"{API}/modules/{mod['id']}/", json={"menu_permission": "nope.nope"})
        assert r.status_code == 422

    async def test_patch_unknown_module_404(self, admin):
        assert (await admin.patch(f"{API}/modules/{ZERO}/", json={"name": "x"})).status_code == 404

    async def test_cannot_create_or_delete_modules(self, admin):
        assert (await admin.post(f"{API}/modules/", json={"name": "x"})).status_code in (404, 405)
        mod = (await admin.get(f"{API}/modules/")).json()[0]
        assert (await admin.delete(f"{API}/modules/{mod['id']}/")).status_code in (404, 405)

    async def test_menu_change_is_audited(self, admin):
        mod = (await admin.get(f"{API}/modules/")).json()[-1]
        await admin.patch(f"{API}/modules/{mod['id']}/", json={"icon": "plane-test"})
        events = (await admin.get(f"{API}/audit/?entity=module&entity_id={mod['id']}")).json()[
            "data"
        ]
        assert events and events[0]["action"] == "update"


class TestPermissionCatalogClosed:
    async def test_lists_exactly_the_seeded_catalog(self, admin):
        r = await admin.get(f"{API}/permissions/")
        assert r.status_code == 200
        codes = {p["code"] for p in r.json()}
        extra = codes - FULL_CATALOG
        assert FULL_CATALOG <= codes
        # only types created by other tests may add derived permissions
        assert all(c.endswith((".sin_archivos", ".legacy_aircraft_text")) for c in extra)

    async def test_entry_shape(self, admin):
        entry = (await admin.get(f"{API}/permissions/")).json()[0]
        assert {"id", "code", "name", "module_code"} <= entry.keys()

    async def test_filter_by_module(self, admin):
        r = await admin.get(f"{API}/permissions/?module_code=users")
        codes = {p["code"] for p in r.json()}
        assert codes == {"users.view", "users.manage"}

    async def test_post_is_not_possible(self, admin):
        r = await admin.post(
            f"{API}/permissions/", json={"code": "x.y", "name": "x", "module_code": "users"}
        )
        assert r.status_code in (404, 405)
        assert "x.y" not in {p["code"] for p in (await admin.get(f"{API}/permissions/")).json()}

    async def test_patch_put_delete_are_not_possible(self, admin):
        pid = (await admin.get(f"{API}/permissions/")).json()[0]["id"]
        for method in ("patch", "put", "delete"):
            r = await admin.request(method.upper(), f"{API}/permissions/{pid}/", json={"name": "x"})
            assert r.status_code in (404, 405), method

    async def test_requires_a_role_or_user_admin_permission(self, pilot, anon):
        assert (await pilot.get(f"{API}/permissions/")).status_code == 403
        assert (await anon.get(f"{API}/permissions/")).status_code == 401

    async def test_users_view_is_enough_to_read(self, new_account):
        api, _, _ = await new_account(["users.view"])
        assert (await api.get(f"{API}/permissions/")).status_code == 200

    async def test_creating_a_role_with_an_invented_permission_fails(self, admin, uniq):
        r = await admin.post(
            f"{API}/roles/", json={"name": f"Rol {uniq()}", "permissions": ["made.up"]}
        )
        assert r.status_code == 422
        assert "made.up" not in {p["code"] for p in (await admin.get(f"{API}/permissions/")).json()}
