"""Passenger counts and other numeric fields never accept negative values (and passengers are
whole numbers); the official rate of the day is proposed to any signed-in user."""

import pytest

from tests.e2e.conftest import API
from tests.e2e.tickets_support import OPERATION, TICKETS, make_operation

RATE = f"{API}/exchange-rate/"


async def create(api, **payload):
    return await api.post(
        TICKETS,
        json={"type_code": "operation", "title": "Vuelo", "payload": {**OPERATION, **payload}},
    )


class TestPassengers:
    @pytest.mark.parametrize("value", [-1, -20, -0.5, "-3"])
    async def test_negative_is_refused(self, admin, value):
        r = await create(admin, passengers=value)
        assert r.status_code == 422, r.text
        assert "passengers" in str(r.json())

    @pytest.mark.parametrize("value", [2.5, "1.5"])
    async def test_a_fraction_is_refused(self, admin, value):
        assert (await create(admin, passengers=value)).status_code == 422

    async def test_absurdly_large_is_refused(self, admin):
        assert (await create(admin, passengers=10**9)).status_code == 422

    @pytest.mark.parametrize("value", [0, 5, "7"])
    async def test_whole_non_negative_is_accepted(self, admin, value):
        op = await make_operation(admin, passengers=value)
        assert op["payload"]["passengers"] == int(value)

    async def test_editing_to_negative_is_refused_and_keeps_the_old_value(self, admin):
        op = await make_operation(admin, passengers=4)
        r = await admin.patch(f"{TICKETS}{op['id']}/", json={"payload": {"passengers": -2}})
        assert r.status_code == 422, r.text
        again = (await admin.get(f"{TICKETS}{op['id']}/")).json()
        assert again["payload"]["passengers"] == 4


class TestOtherNumbers:
    async def test_a_negative_exchange_rate_is_refused(self, admin):
        r = await create(admin, exchange_rate=-5)
        assert r.status_code == 422, r.text
        assert "exchange_rate" in str(r.json())

    async def test_a_positive_exchange_rate_is_accepted(self, admin):
        op = await make_operation(admin, exchange_rate=899.27)
        assert op["payload"]["exchange_rate"] == 899.27


class TestExchangeRateEndpoint:
    async def test_requires_a_session(self, anon):
        assert (await anon.get(RATE)).status_code == 401

    async def test_any_signed_in_user_gets_a_positive_rate(self, pilot):
        r = await pilot.get(RATE)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rate"] > 0 and body["source"] == "oficial"
        assert body["effective_date"] and body["fetched_at"]
