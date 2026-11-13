"""The expense supplier is a list Transvalor loads, not free text."""

from tests.e2e.conftest import API
from tests.e2e.tickets_support import make_operation, post_expense

ITEMS = f"{API}/catalog-items/"


class TestExpenseSupplier:
    async def test_the_catalog_starts_empty_so_a_typed_name_is_refused(self, admin):
        op = await make_operation(admin)
        items = (await admin.get(f"{ITEMS}?catalog=suppliers&limit=100")).json()
        assert items["total"] == 0
        r = await post_expense(admin, op, supplier="Cualquiera")
        assert r.status_code == 422, r.text
        assert "supplier" in str(r.json())

    async def test_an_item_loaded_by_the_administrator_is_accepted(self, admin):
        op = await make_operation(admin)
        made = await admin.post(ITEMS, json={"catalog": "suppliers", "name": "Estación El Sol"})
        assert made.status_code == 201, made.text
        code = made.json()["code"]
        r = await post_expense(admin, op, supplier=code)
        assert r.status_code == 201, r.text
        assert r.json()["payload"]["supplier"] == code

    async def test_leaving_it_empty_is_allowed(self, admin):
        op = await make_operation(admin)
        r = await post_expense(admin, op)
        assert r.status_code == 201, r.text
        assert "supplier" not in r.json()["payload"]
