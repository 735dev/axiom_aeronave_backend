"""The replay window of an Idempotency-Key is a setting (own server, 2 second TTL)."""

import asyncio

import pytest

from tests.e2e.live_support import live_server  # noqa: F401  (fixture)
from tests.e2e.tickets_support import OPERATION, TICKETS, new_key

TTL = 2


@pytest.fixture(scope="module")
def short_lived(live_server):  # noqa: F811
    return live_server(IDEMPOTENCY_TTL_SECONDS=str(TTL), IDEMPOTENCY_PURGE_INTERVAL_SECONDS="1")


@pytest.fixture
async def admin_remote(short_lived):
    client = short_lived()
    r = await client.login("super@transvalor.com")
    assert r.status_code == 200, r.text
    return client.as_token(r.json()["token"])


def operation(title: str) -> dict:
    return {"type_code": "operation", "title": title, "payload": dict(OPERATION)}


class TestTtl:
    async def test_inside_the_window_the_key_replays(self, admin_remote):
        key = new_key()
        first = await admin_remote.post(
            TICKETS, json=operation("a"), headers={"Idempotency-Key": key}
        )
        again = await admin_remote.post(
            TICKETS, json=operation("a"), headers={"Idempotency-Key": key}
        )
        assert first.status_code == 201 and again.status_code == 201
        assert again.headers["idempotent-replay"] == "true"
        assert first.json()["id"] == again.json()["id"]

    async def test_after_the_window_the_key_is_forgotten(self, admin_remote):
        key = new_key()
        first = await admin_remote.post(
            TICKETS, json=operation("b"), headers={"Idempotency-Key": key}
        )
        assert first.status_code == 201
        # Inside the window a different body under the same key is a client bug...
        clash = await admin_remote.post(
            TICKETS, json=operation("c"), headers={"Idempotency-Key": key}
        )
        assert clash.status_code == 422 and clash.json()["codigo"] == "idempotencia"
        await asyncio.sleep(TTL + 1.5)
        # ...after it the key is free again: no replay, no conflict, a new record.
        later = await admin_remote.post(
            TICKETS, json=operation("c"), headers={"Idempotency-Key": key}
        )
        assert later.status_code == 201 and "idempotent-replay" not in later.headers
        assert later.json()["id"] != first.json()["id"]

    async def test_the_same_body_after_the_window_is_a_new_request(self, admin_remote):
        key = new_key()
        first = await admin_remote.post(
            TICKETS, json=operation("d"), headers={"Idempotency-Key": key}
        )
        await asyncio.sleep(TTL + 1.5)
        later = await admin_remote.post(
            TICKETS, json=operation("d"), headers={"Idempotency-Key": key}
        )
        assert later.status_code == 201 and later.json()["id"] != first.json()["id"]
