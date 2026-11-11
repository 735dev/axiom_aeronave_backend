"""Captain, FBO and fuel supplier are lists, not free text: the captain comes from the active
users with the pilot role; the other two from catalogs that Transvalor loads."""

from tests.e2e.conftest import API
from tests.e2e.tickets_support import OPERATION, TICKETS, make_operation

OPTIONS = f"{API}/user-options/"
ITEMS = f"{API}/catalog-items/"
PILOT = "Cap. Rodríguez"


async def create(api, **payload):
    return await api.post(
        TICKETS,
        json={"type_code": "operation", "title": "Vuelo", "payload": {**OPERATION, **payload}},
    )


class TestUserOptions:
    async def test_requires_a_session(self, anon):
        assert (await anon.get(f"{OPTIONS}?role=pilot")).status_code == 401

    async def test_lists_only_the_active_people_of_that_role(self, pilot):
        r = await pilot.get(f"{OPTIONS}?role=pilot")
        assert r.status_code == 200, r.text
        names = [o["value"] for o in r.json()]
        assert PILOT in names
        assert "Gerencia de Operaciones" not in names
        assert all(o["value"] == o["label"] for o in r.json())

    async def test_only_the_name_leaves_the_server(self, pilot):
        body = (await pilot.get(f"{OPTIONS}?role=pilot")).json()
        assert all(set(o) == {"value", "label"} for o in body)
        assert "@" not in str(body)

    async def test_a_role_without_people_is_an_empty_list(self, pilot):
        r = await pilot.get(f"{OPTIONS}?role=nobody_has_this")
        assert r.status_code == 200 and r.json() == []

    async def test_a_malformed_role_is_refused(self, pilot):
        for role in ("", "x", "Pilot", "a b", "pilot;drop", "a" * 41):
            assert (await pilot.get(f"{OPTIONS}?role={role}")).status_code == 422, role


class TestCaptain:
    async def test_a_pilot_is_accepted(self, admin):
        op = await make_operation(admin, captain=PILOT)
        assert op["payload"]["captain"] == PILOT

    async def test_someone_who_is_not_a_pilot_is_refused(self, admin):
        r = await create(admin, captain="Gerencia de Operaciones")
        assert r.status_code == 422, r.text
        assert "captain" in str(r.json())

    async def test_an_invented_name_is_refused(self, admin):
        assert (await create(admin, captain="Juan Inventado")).status_code == 422

    async def test_clearing_it_is_allowed(self, admin):
        op = await make_operation(admin, captain=PILOT)
        r = await admin.patch(f"{TICKETS}{op['id']}/", json={"payload": {"captain": ""}})
        assert r.status_code == 200, r.text
        assert "captain" not in r.json()["payload"]


class TestFboAndFuelSupplier:
    async def test_the_catalogs_start_empty_so_nothing_is_accepted(self, admin):
        for catalog, key in (("fbos", "fbo_name"), ("fuel_suppliers", "fuel_supplier")):
            items = (await admin.get(f"{ITEMS}?catalog={catalog}&limit=100")).json()
            assert items["total"] == 0
            r = await create(admin, **{key: "Cualquiera"})
            assert r.status_code == 422, (key, r.text)

    async def test_an_item_loaded_by_the_administrator_is_accepted(self, admin):
        for catalog, key, name in (
            ("fbos", "fbo_name", "FBO Maiquetía Test"),
            ("fuel_suppliers", "fuel_supplier", "Broker Test"),
        ):
            made = await admin.post(ITEMS, json={"catalog": catalog, "name": name})
            assert made.status_code == 201, made.text
            code = made.json()["code"]
            op = await make_operation(admin, **{key: code})
            assert op["payload"][key] == code

    async def test_a_pilot_cannot_load_items(self, pilot):
        r = await pilot.post(ITEMS, json={"catalog": "fbos", "name": "Intruso"})
        assert r.status_code == 403
