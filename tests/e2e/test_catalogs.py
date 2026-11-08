from tests.e2e.conftest import API, Api, create_user, role_by_code, role_id

ZERO = "00000000-0000-0000-0000-000000000000"
ITEMS = f"{API}/catalog-items/"


async def find(admin, catalog, code):
    data = (await admin.get(f"{ITEMS}?catalog={catalog}&limit=100")).json()["data"]
    return next(i for i in data if i["code"] == code)


class TestCatalogDefinitions:
    async def test_lists_nine_catalogs_with_counts(self, admin):
        r = await admin.get(f"{API}/catalogs/")
        body = r.json()
        assert r.status_code == 200 and isinstance(body, list) and len(body) == 9
        counts = {c["key"]: c["items_count"] for c in body}
        assert counts["aircrafts"] >= 3 and counts["airports"] >= 5 and counts["currencies"] >= 2

    async def test_pilot_cannot_list_definitions(self, pilot):
        assert (await pilot.get(f"{API}/catalogs/")).status_code == 403

    async def test_catalogs_view_can(self, new_account):
        api, _, _ = await new_account(["catalogs.view"])
        assert (await api.get(f"{API}/catalogs/")).status_code == 200


class TestCatalogItemsRead:
    async def test_pilot_can_read_items_for_dropdowns(self, pilot):
        r = await pilot.get(f"{ITEMS}?catalog=aircrafts")
        assert r.status_code == 200
        assert {i["code"] for i in r.json()["data"]} >= {"YV2323"}

    async def test_filter_by_catalog(self, pilot):
        data = (await pilot.get(f"{ITEMS}?catalog=airports&limit=100")).json()["data"]
        assert data and {i["catalog"] for i in data} == {"airports"}

    async def test_unknown_catalog_filter_422(self, pilot):
        assert (await pilot.get(f"{ITEMS}?catalog=nope")).status_code == 422

    async def test_sorted_by_sort_order(self, pilot):
        orders = [
            i["sort_order"] for i in (await pilot.get(f"{ITEMS}?catalog=aircrafts")).json()["data"]
        ]
        assert orders == sorted(orders)

    async def test_search(self, pilot):
        data = (await pilot.get(f"{ITEMS}?catalog=aircrafts&q=king")).json()["data"]
        assert data and all("king" in i["name"].lower() for i in data)

    async def test_retrieve_by_id(self, pilot, admin):
        item = await find(admin, "aircrafts", "YV2323")
        r = await pilot.get(f"{ITEMS}{item['id']}/")
        assert r.status_code == 200 and r.json()["code"] == "YV2323"

    async def test_retrieve_unknown_404(self, pilot):
        assert (await pilot.get(f"{ITEMS}{ZERO}/")).status_code == 404

    async def test_requires_token(self, anon):
        assert (await anon.get(ITEMS)).status_code == 401

    async def test_active_filter(self, pilot):
        data = (await pilot.get(f"{ITEMS}?is_active=true&limit=100")).json()["data"]
        assert data and all(i["is_active"] for i in data)

    async def test_matches_seed_counts(self, pilot):
        expected = {
            "expense_concepts": 9,
            "units": 4,
            "currencies": 2,
            "operation_kinds": 4,
            "operation_scopes": 2,
            "aircraft_statuses": 3,
            "aircrafts": 3,
            "airports": 5,
            "locations": 3,
        }
        for key, n in expected.items():
            assert (await pilot.get(f"{ITEMS}?catalog={key}&limit=100")).json()["total"] >= n, key


class TestCatalogItemsWrite:
    async def test_pilot_cannot_write(self, pilot):
        assert (
            await pilot.post(ITEMS, json={"catalog": "units", "name": "Galón"})
        ).status_code == 403
        assert (await pilot.patch(f"{ITEMS}{ZERO}/", json={"name": "xx"})).status_code == 403
        assert (await pilot.delete(f"{ITEMS}{ZERO}/")).status_code == 403

    async def test_catalogs_view_cannot_write(self, new_account):
        api, _, _ = await new_account(["catalogs.view"])
        assert (
            await api.post(ITEMS, json={"catalog": "units", "name": "Galón"})
        ).status_code == 403

    async def test_create_generates_code_and_sort_order(self, admin, uniq):
        name = f"Unidad {uniq()}"
        r = await admin.post(ITEMS, json={"catalog": "units", "name": name})
        item = r.json()
        assert r.status_code == 201
        assert item["code"] and item["is_system"] is False and item["is_active"] is True
        assert item["sort_order"] >= 1

    async def test_create_with_explicit_code(self, admin, uniq):
        code = f"c{uniq()}"
        r = await admin.post(
            ITEMS,
            json={"catalog": "airports", "name": "Aeropuerto Nuevo", "code": code, "extra": "XYZ"},
        )
        assert r.status_code == 201 and r.json()["code"] == code and r.json()["extra"] == "XYZ"

    async def test_duplicate_code_in_same_catalog_422(self, admin, uniq):
        code = f"d{uniq()}"
        await admin.post(ITEMS, json={"catalog": "airports", "name": "Uno", "code": code})
        assert (
            await admin.post(ITEMS, json={"catalog": "airports", "name": "Dos", "code": code})
        ).status_code == 422

    async def test_same_code_in_other_catalog_allowed(self, admin, uniq):
        code = f"s{uniq()}"
        assert (
            await admin.post(ITEMS, json={"catalog": "airports", "name": "Uno", "code": code})
        ).status_code == 201
        assert (
            await admin.post(ITEMS, json={"catalog": "units", "name": "Dos", "code": code})
        ).status_code == 201

    async def test_unknown_catalog_422(self, admin):
        assert (
            await admin.post(ITEMS, json={"catalog": "nope", "name": "Algo"})
        ).status_code == 422

    async def test_invalid_code_chars_422(self, admin):
        assert (
            await admin.post(ITEMS, json={"catalog": "units", "name": "Algo", "code": "a b/c"})
        ).status_code == 422

    async def test_create_ignores_forged_flags(self, admin, uniq):
        r = await admin.post(
            ITEMS,
            json={
                "catalog": "units",
                "name": f"Forjada {uniq()}",
                "is_system": True,
                "is_active": False,
            },
        )
        assert r.json()["is_system"] is False and r.json()["is_active"] is True

    async def test_update_name_and_deactivate(self, admin, uniq):
        item = (
            await admin.post(ITEMS, json={"catalog": "units", "name": f"Editable {uniq()}"})
        ).json()
        r = await admin.patch(
            f"{ITEMS}{item['id']}/", json={"name": "Renombrada", "is_active": False}
        )
        assert (
            r.status_code == 200
            and r.json()["name"] == "Renombrada"
            and r.json()["is_active"] is False
        )

    async def test_code_and_catalog_are_immutable(self, admin, uniq):
        item = (await admin.post(ITEMS, json={"catalog": "units", "name": f"Fija {uniq()}"})).json()
        await admin.patch(f"{ITEMS}{item['id']}/", json={"code": "otro", "catalog": "airports"})
        after = (await admin.get(f"{ITEMS}{item['id']}/")).json()
        assert after["code"] == item["code"] and after["catalog"] == "units"

    async def test_delete_unused_item(self, admin, uniq):
        item = (
            await admin.post(ITEMS, json={"catalog": "units", "name": f"Borrable {uniq()}"})
        ).json()
        assert (await admin.delete(f"{ITEMS}{item['id']}/")).status_code == 204
        assert (await admin.get(f"{ITEMS}{item['id']}/")).status_code == 404

    async def test_delete_unknown_404(self, admin):
        assert (await admin.delete(f"{ITEMS}{ZERO}/")).status_code == 404

    async def test_malformed_id_422(self, admin):
        assert (await admin.delete(f"{ITEMS}not-a-uuid/")).status_code == 422

    async def test_system_item_cannot_be_deleted_or_deactivated(self, admin):
        data = (await admin.get(f"{ITEMS}?limit=100")).json()["data"]
        system = [i for i in data if i["is_system"]]
        if not system:
            return
        assert (await admin.delete(f"{ITEMS}{system[0]['id']}/")).status_code == 422
        assert (
            await admin.patch(f"{ITEMS}{system[0]['id']}/", json={"is_active": False})
        ).status_code == 422


class TestReferentialProtection:
    async def test_aircraft_in_use_cannot_be_deleted(self, admin):
        item = await find(admin, "aircrafts", "YV2323")
        r = await admin.delete(f"{ITEMS}{item['id']}/")
        assert r.status_code == 422
        assert (await admin.get(f"{ITEMS}{item['id']}/")).status_code == 200

    async def test_location_in_use_cannot_be_deleted(self, admin):
        item = await find(admin, "locations", "charallave")
        assert (await admin.delete(f"{ITEMS}{item['id']}/")).status_code == 422

    async def test_new_item_becomes_protected_once_assigned_to_a_user(self, admin, uniq):
        code = f"loc{uniq()}"
        item = (
            await admin.post(
                ITEMS, json={"catalog": "locations", "name": "Sede Nueva", "code": code}
            )
        ).json()
        await create_user(
            admin, f"{uniq()}@example.com", await role_id(admin, "pilot"), location=code
        )
        assert (await admin.delete(f"{ITEMS}{item['id']}/")).status_code == 422

    async def test_deactivate_instead_of_delete(self, admin, uniq):
        code = f"ac{uniq()}"
        item = (
            await admin.post(ITEMS, json={"catalog": "aircrafts", "name": "YV9999", "code": code})
        ).json()
        await create_user(
            admin, f"{uniq()}@example.com", await role_id(admin, "pilot"), aircraft_id=code
        )
        assert (
            await admin.patch(f"{ITEMS}{item['id']}/", json={"is_active": False})
        ).status_code == 200

    async def test_deactivated_item_cannot_be_assigned_to_new_user(self, admin, uniq):
        code = f"ac{uniq()}"
        item = (
            await admin.post(ITEMS, json={"catalog": "aircrafts", "name": "YV8888", "code": code})
        ).json()
        await admin.patch(f"{ITEMS}{item['id']}/", json={"is_active": False})
        r = await admin.post(
            f"{API}/users/",
            json={
                "full_name": "X Y",
                "email": f"{uniq()}@example.com",
                "role_id": await role_id(admin, "pilot"),
                "aircraft_id": code,
            },
        )
        assert r.status_code == 422

    async def test_unused_item_deletable_after_creation(self, admin, uniq):
        code = f"loc{uniq()}"
        item = (
            await admin.post(
                ITEMS, json={"catalog": "locations", "name": "Sede Libre", "code": code}
            )
        ).json()
        assert (await admin.delete(f"{ITEMS}{item['id']}/")).status_code == 204

    async def test_changes_are_audited(self, admin, uniq):
        item = (
            await admin.post(ITEMS, json={"catalog": "units", "name": f"Auditada {uniq()}"})
        ).json()
        await admin.patch(f"{ITEMS}{item['id']}/", json={"name": "Auditada 2"})
        await admin.delete(f"{ITEMS}{item['id']}/")
        events = (await admin.get(f"{API}/audit/?entity=catalog&entity_id={item['id']}")).json()[
            "data"
        ]
        assert {"create", "update", "delete"} <= {e["action"] for e in events}
