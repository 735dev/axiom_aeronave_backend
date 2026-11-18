from datetime import date

import pytest

from tests.e2e.tickets_support import (
    OPERATION,
    TICKETS,
    get_ticket,
    make_expense,
    make_operation,
    patch,
    post_expense,
)


class TestDualCurrency:
    async def test_dollar_expense_has_rate_one(self, admin):
        exp = await make_expense(admin, await make_operation(admin), amount=55.5)
        assert exp["money"]["rate"] == 1 and exp["money"]["amount_base"] == 55.5
        assert exp["money"]["base_currency"] == "USD" and exp["money"]["rate_source"] == "base"

    async def test_bolivares_use_the_official_quote_and_freeze_it(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=100)
        assert exp["money"]["amount_base"] == 1
        assert exp["money"]["rate"] == 0.01
        assert exp["payload"]["exchange_rate"] == 100
        assert exp["money"]["currency_name"] == "Bolívar"

    async def test_the_client_cannot_write_the_rate_on_the_expense(self, admin):
        op = await make_operation(admin)
        r = await post_expense(admin, op, currency="VES", amount=500, exchange_rate=50)
        assert r.status_code == 422

    async def test_editing_a_note_does_not_revalue(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=400)
        edited = await patch(admin, exp, payload={"notes": "nota nueva"})
        assert edited.status_code == 200
        assert edited.json()["money"]["amount_base"] == 4

    async def test_reading_never_revalues(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=400)
        again = await get_ticket(admin, exp)
        assert again["money"]["amount_base"] == 4 and again["money"]["rate"] == 0.01

    async def test_listing_never_revalues(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=400)
        rows = (await admin.get(f"/api/v1/tickets/?parent_id={op['id']}&type_code=expense")).json()
        assert rows["data"][0]["id"] == exp["id"]
        assert rows["data"][0]["money"]["amount_base"] == 4

    async def test_changing_the_amount_revalues_with_the_frozen_rate(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=400)
        edited = await patch(admin, exp, payload={"amount": 800})
        assert edited.json()["money"]["amount_base"] == 8
        assert edited.json()["money"]["rate"] == 0.01

    async def test_changing_the_currency_revalues(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=400)
        edited = await patch(admin, exp, payload={"currency": "USD"})
        assert edited.json()["money"]["amount_base"] == 400
        assert edited.json()["money"]["rate"] == 1

    async def test_switching_back_to_bolivares_uses_the_official_quote(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="USD", amount=10)
        r = await patch(admin, exp, payload={"currency": "VES"})
        assert r.status_code == 200
        assert r.json()["money"]["amount_base"] == 0.1
        assert r.json()["payload"]["exchange_rate"] == 100

    async def test_the_operation_form_rejects_an_exchange_rate(self, admin):
        body = {
            "type_code": "operation",
            "title": "x",
            "payload": {**OPERATION, "exchange_rate": -5},
        }
        r = await admin.post(TICKETS, json=body)
        assert r.status_code == 422 and "exchange_rate" in r.json()["errors"]

    async def test_conversion_rounds_to_cents(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=1)
        assert exp["money"]["amount_base"] == 0.01

    async def test_big_conversion_is_exact(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=94800)
        assert exp["money"]["amount_base"] == 948

    async def test_rated_at_is_set_once(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, currency="VES", amount=400)
        edited = await patch(admin, exp, payload={"notes": "x"})
        assert edited.json()["money"]["rated_at"] == exp["money"]["rated_at"]


class TestAmountValidation:
    @pytest.mark.parametrize("amount", [-1, "abc", "1e999", 10**15, 12.345])
    async def test_invalid_amounts_are_rejected(self, admin, amount):
        op = await make_operation(admin)
        assert (await post_expense(admin, op, amount=amount)).status_code == 422

    async def test_zero_amount_is_allowed(self, admin):
        exp = await make_expense(admin, await make_operation(admin), amount=0)
        assert exp["money"]["amount_base"] == 0

    async def test_amount_as_numeric_string(self, admin):
        exp = await make_expense(admin, await make_operation(admin), amount="25.50")
        assert exp["money"]["amount_base"] == 25.5

    async def test_amount_is_required(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            "/api/v1/tickets/",
            json={
                "type_code": "expense",
                "parent_id": op["id"],
                "payload": {"concept": "fuel", "currency": "USD"},
            },
        )
        assert r.status_code == 422 and "amount" in r.json()["errors"]

    async def test_unknown_currency_rejected(self, admin):
        op = await make_operation(admin)
        assert (await post_expense(admin, op, currency="GBP")).status_code == 422

    async def test_client_cannot_send_the_base_amount(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            "/api/v1/tickets/",
            json={
                "type_code": "expense",
                "parent_id": op["id"],
                "money": {"amount": 1, "amount_base": 0.01, "rate": 100, "currency": "USD"},
                "amount_base": 0.01,
                "payload": {"concept": "fuel", "amount": 900, "currency": "USD"},
            },
        )
        assert r.status_code in (201, 422)
        if r.status_code == 201:
            assert r.json()["money"]["amount_base"] == 900 and r.json()["money"]["rate"] == 1

    async def test_client_cannot_patch_the_base_amount(self, admin):
        exp = await make_expense(admin, await make_operation(admin), amount=40)
        await admin.patch(
            f"/api/v1/tickets/{exp['id']}/",
            json={"amount_base": 1, "money": {"amount_base": 1}},
        )
        assert (await get_ticket(admin, exp))["money"]["amount_base"] == 40

    async def test_ticket_without_amount_field_has_no_money(self, admin):
        op = await make_operation(admin)
        assert op["money"] is None

    async def test_removing_nothing_keeps_money_on_unrelated_edit(self, admin):
        exp = await make_expense(admin, await make_operation(admin), amount=40)
        edited = await patch(admin, exp, title="Otro título")
        assert edited.json()["money"] == exp["money"]

    async def test_money_edit_is_audited(self, admin):
        exp = await make_expense(admin, await make_operation(admin), amount=40)
        await patch(admin, exp, payload={"amount": 60})
        events = (await admin.get(f"/api/v1/tickets/{exp['id']}/history/?limit=100")).json()["data"]
        update = next(e for e in events if e["action"] == "update")
        change = next(c for c in update["changes"] if c["field"] == "amount")
        assert change["from"] == 40 and change["to"] == 60


class TestAccountingDate:
    async def test_expense_without_date_uses_the_creation_day(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        created = date.fromisoformat(exp["created_at"][:10])
        assert abs((date.fromisoformat(exp["accounting_date"]) - created).days) <= 1

    async def test_expense_date_field_sets_the_accounting_date(self, admin):
        exp = await make_expense(admin, await make_operation(admin), expense_date="2026-01-15")
        assert exp["accounting_date"] == "2026-01-15"

    async def test_changing_the_date_moves_it(self, admin):
        exp = await make_expense(admin, await make_operation(admin), expense_date="2026-01-15")
        edited = await patch(admin, exp, payload={"expense_date": "2026-02-01"})
        assert edited.json()["accounting_date"] == "2026-02-01"
