"""GET /tickets/activity/: counts of operations and of the work attached to them."""

from tests.e2e.conftest import API
from tests.e2e.tickets_support import make_expense, make_operation


async def activity(api, **query) -> dict:
    params = "&".join(f"{k}={v}" for k, v in query.items())
    r = await api.get(f"{API}/tickets/activity/?{params}")
    assert r.status_code == 200, r.text
    return r.json()


class TestOperationsAndWork:
    async def test_an_operation_is_counted_on_its_date_and_not_as_work(self, ops):
        await make_operation(ops, date="2031-04-02")
        flights = await activity(ops, scope="operations", **{"from": "2031-04-02", "to": "2031-04-02"})
        assert flights["total"] == 1
        assert flights["open_count"] == 1 and flights["closed_count"] == 0
        assert flights["by_aircraft"][0]["key"] == "YV2323"
        assert flights["by_status"][0]["label"] == "Programada"
        work = await activity(ops, scope="work", **{"from": "2031-04-02", "to": "2031-04-02"})
        assert work["total"] == 0

    async def test_an_expense_counts_as_work_and_not_as_an_operation(self, ops, pilot):
        op = await make_operation(ops, date="2031-05-03")
        await make_expense(pilot, op, expense_date="2031-05-03", amount=20)
        flights = await activity(ops, scope="operations", **{"from": "2031-05-03", "to": "2031-05-03"})
        work = await activity(ops, scope="work", **{"from": "2031-05-03", "to": "2031-05-03"})
        assert flights["total"] == 1
        assert work["total"] == 1
        assert work["by_type"][0]["key"] == "expense"
        assert work["by_type"][0]["count"] == 1

    async def test_a_day_without_flights_is_empty(self, ops):
        body = await activity(ops, scope="operations", **{"from": "1999-01-01", "to": "1999-01-01"})
        assert body["total"] == 0
        assert body["by_aircraft"] == [] and body["by_status"] == []


class TestPermission:
    async def test_requires_a_scope(self, ops):
        r = await ops.get(f"{API}/tickets/activity/")
        assert r.status_code == 422

    async def test_requires_login(self, anon):
        assert (await anon.get(f"{API}/tickets/activity/?scope=operations")).status_code == 401

    async def test_a_pilot_without_analytics_gets_403(self, pilot):
        assert (await pilot.get(f"{API}/tickets/activity/?scope=operations")).status_code == 403
