"""GET /tickets/totals/: both currencies and the breakdown by concept."""

import uuid

from tests.e2e.closing_support import totals
from tests.e2e.conftest import API
from tests.e2e.tickets_support import make_expense, make_operation, patch


def by_currency(body: dict) -> dict:
    return {c["currency"]: c for c in body["by_currency"]}


def by_concept(body: dict) -> dict:
    return {c["label"]: c for c in body["by_concept"]}


class TestShape:
    async def test_an_operation_without_expenses_totals_zero(self, ops):
        body = await totals(ops, parent_id=(await make_operation(ops))["id"])
        assert body["base_currency"] == "USD"
        assert body["committed_base"] == body["pending_base"] == body["excluded_base"] == 0
        assert body["by_currency"] == [] and body["by_concept"] == []

    async def test_a_dollar_expense(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=100)
        body = await totals(ops, parent_id=op["id"])
        usd = by_currency(body)["USD"]
        assert usd["amount"] == 100 and usd["amount_base"] == 100 and usd["count"] == 1
        assert usd["currency_name"]

    async def test_both_currencies_are_kept_apart(self, ops):
        op = await make_operation(ops, exchange_rate=50)
        await make_expense(ops, op, amount=100, currency="USD")
        await make_expense(ops, op, amount=5000, currency="VES")
        body = await totals(ops, parent_id=op["id"])
        cur = by_currency(body)
        assert cur["USD"]["amount"] == 100 and cur["VES"]["amount"] == 5000
        assert cur["VES"]["amount_base"] == 100
        assert body["pending_base"] == 200

    async def test_amounts_add_up_per_currency(self, ops):
        op = await make_operation(ops)
        for amount in (10.10, 20.20, 30.30):
            await make_expense(ops, op, amount=amount)
        usd = by_currency(await totals(ops, parent_id=op["id"]))["USD"]
        assert usd["amount"] == 60.6 and usd["count"] == 3

    async def test_money_is_rounded_to_cents(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=0.1)
        await make_expense(ops, op, amount=0.2)
        assert by_currency(await totals(ops, parent_id=op["id"]))["USD"]["amount"] == 0.3

    async def test_by_concept_uses_the_catalog_labels(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, concept="fuel", amount=100)
        await make_expense(ops, op, concept="fuel", amount=50)
        await make_expense(ops, op, concept="catering", amount=30)
        body = await totals(ops, parent_id=op["id"])
        concepts = by_concept(body)
        assert (
            concepts["Combustible"]["amount_base"] == 150 and concepts["Combustible"]["count"] == 2
        )
        assert concepts["Catering"]["amount_base"] == 30
        assert concepts["Combustible"]["key"] == "fuel"

    async def test_concepts_are_ordered_by_amount_desc(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, concept="catering", amount=30)
        await make_expense(ops, op, concept="fuel", amount=500)
        amounts = [c["amount_base"] for c in (await totals(ops, parent_id=op["id"]))["by_concept"]]
        assert amounts == sorted(amounts, reverse=True)

    async def test_a_foreign_currency_expense_is_counted_at_its_frozen_rate(self, ops):
        op = await make_operation(ops, exchange_rate=40)
        await make_expense(ops, op, amount=400, currency="VES")
        assert (await totals(ops, parent_id=op["id"]))["pending_base"] == 10


class TestStatesAreKeptApart:
    async def test_open_expense_is_pending(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=100)
        body = await totals(ops, parent_id=op["id"])
        assert body["pending_base"] == 100 and body["committed_base"] == 0

    async def test_a_verified_expense_is_committed(self, ops):
        op = await make_operation(ops)
        e = await make_expense(ops, op, amount=100)
        await patch(ops, e, status_code="verified")
        body = await totals(ops, parent_id=op["id"])
        assert body["committed_base"] == 100 and body["pending_base"] == 0

    async def test_a_rejected_expense_is_excluded(self, ops):
        op = await make_operation(ops)
        e = await make_expense(ops, op, amount=100)
        await patch(ops, e, status_code="rejected")
        body = await totals(ops, parent_id=op["id"])
        assert body["excluded_base"] == 100
        assert body["committed_base"] == body["pending_base"] == 0

    async def test_all_three_buckets_at_once(self, ops):
        op = await make_operation(ops)
        a, b = await make_expense(ops, op, amount=100), await make_expense(ops, op, amount=30)
        c = await make_expense(ops, op, amount=7)
        await patch(ops, a, status_code="verified")
        await patch(ops, c, status_code="rejected")
        body = await totals(ops, parent_id=op["id"])
        assert (body["committed_base"], body["pending_base"], body["excluded_base"]) == (100, 30, 7)

    async def test_status_filter_narrows_the_sum(self, ops):
        op = await make_operation(ops)
        a, _ = await make_expense(ops, op, amount=100), await make_expense(ops, op, amount=30)
        await patch(ops, a, status_code="verified")
        body = await totals(ops, parent_id=op["id"], status_code="verified")
        assert body["committed_base"] == 100 and body["pending_base"] == 0


class TestFilters:
    async def test_other_operations_are_left_out(self, ops):
        a, b = await make_operation(ops), await make_operation(ops)
        await make_expense(ops, b, amount=999)
        assert (await totals(ops, parent_id=a["id"]))["pending_base"] == 0

    async def test_date_range_uses_the_accounting_date(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=10, expense_date="2019-03-10")
        await make_expense(ops, op, amount=20, expense_date="2019-04-10")
        body = await totals(ops, parent_id=op["id"], **{"from": "2019-04-01", "to": "2019-04-30"})
        assert body["pending_base"] == 20

    async def test_the_last_day_of_the_range_is_included(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=10, expense_date="2019-04-30")
        body = await totals(ops, parent_id=op["id"], **{"from": "2019-04-30", "to": "2019-04-30"})
        assert body["pending_base"] == 10

    async def test_filter_by_aircraft(self, ops):
        op = await make_operation(ops, aircraft="YV2323")
        await make_expense(ops, op, amount=15)
        body = await totals(ops, parent_id=op["id"], aircraft="yv2323")
        assert body["pending_base"] == 15
        assert (await totals(ops, parent_id=op["id"], aircraft="YV0000"))["pending_base"] == 0

    async def test_type_filter_to_operations_has_no_money(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=15)
        body = await totals(ops, parent_id=op["id"], type_code="technical_report")
        assert body["pending_base"] == 0

    async def test_search_text_narrows_the_set(self, ops):
        op = await make_operation(ops)
        token = uuid.uuid4().hex[:8]
        from tests.e2e.tickets_support import post_expense

        await post_expense(ops, op, title=f"Hielo {token}", amount=11)
        await make_expense(ops, op, amount=500)
        assert (await totals(ops, parent_id=op["id"], q=token))["pending_base"] == 11

    async def test_open_filter(self, ops):
        op = await make_operation(ops)
        a = await make_expense(ops, op, amount=100)
        await make_expense(ops, op, amount=30)
        await patch(ops, a, status_code="verified")
        assert (await totals(ops, parent_id=op["id"], open="true"))["pending_base"] == 30

    async def test_bad_dates_are_422(self, ops):
        for bad in ("junk", "2019-13-01", "2019-02-30"):
            r = await ops.get(f"{API}/tickets/totals/?from={bad}")
            assert r.status_code == 422, bad

    async def test_a_bad_uuid_is_422(self, ops):
        assert (await ops.get(f"{API}/tickets/totals/?parent_id=zz")).status_code == 422

    async def test_unknown_or_internal_filters_are_ignored(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=15)
        r = await ops.get(
            f"{API}/tickets/totals/?parent_id={op['id']}&password_hash=x&amount_base=1&deleted=1"
        )
        assert r.status_code == 200 and r.json()["pending_base"] == 15

    async def test_the_sum_matches_the_listing(self, ops):
        op = await make_operation(ops)
        for amount in (12, 7.5, 3.25):
            await make_expense(ops, op, amount=amount)
        rows = (
            await ops.get(f"{API}/tickets/?parent_id={op['id']}&type_code=expense&limit=100")
        ).json()["data"]
        assert (
            round(sum(r["money"]["amount_base"] for r in rows), 2)
            == (await totals(ops, parent_id=op["id"]))["pending_base"]
        )


class TestPermissionAndScope:
    async def test_requires_login(self, anon):
        assert (await anon.get(f"{API}/tickets/totals/")).status_code == 401

    async def test_a_pilot_without_analytics_gets_403(self, pilot):
        assert (await pilot.get(f"{API}/tickets/totals/")).status_code == 403

    async def test_analytics_without_ticket_view_is_403_or_empty(self, new_account, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=15)
        only, _, _ = await new_account(["analytics.view"])
        r = await only.get(f"{API}/tickets/totals/?parent_id={op['id']}")
        assert r.status_code in (200, 403)
        if r.status_code == 200:
            assert r.json()["pending_base"] == 0

    async def test_a_viewer_of_other_types_sees_no_money(self, new_account, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=15)
        viewer, _, _ = await new_account(["analytics.view", "tickets.view.operation"])
        body = await totals(viewer, parent_id=op["id"])
        assert body["pending_base"] == 0 and body["by_currency"] == []

    async def test_a_viewer_of_expenses_sees_them(self, new_account, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=15)
        viewer, _, _ = await new_account(["analytics.view", "tickets.view.expense"])
        assert (await totals(viewer, parent_id=op["id"]))["pending_base"] == 15

    async def test_the_scope_is_the_same_as_the_listing(self, new_account, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, amount=15)
        viewer, _, _ = await new_account(["analytics.view", "tickets.view.technical_report"])
        listing = (
            await viewer.get(f"{API}/tickets/?parent_id={op['id']}&type_code=expense")
        ).json()
        assert listing["total"] == 0
        assert (await totals(viewer, parent_id=op["id"]))["pending_base"] == 0

    async def test_totals_is_not_read_as_a_ticket_id(self, ops):
        r = await ops.get(f"{API}/tickets/totals/")
        assert r.status_code == 200 and "base_currency" in r.json()
