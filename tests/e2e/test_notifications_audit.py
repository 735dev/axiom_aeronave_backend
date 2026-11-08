"""Creation and bulk reads of notices are audited, and the trail never carries their content."""

import uuid

from tests.e2e.attachments_support import about, notices
from tests.e2e.conftest import API
from tests.e2e.tickets_support import OPERATION

AUDIT = f"{API}/audit/"


async def notice_events(admin, **query) -> list[dict]:
    params = "&".join(
        f"{k}={v}" for k, v in {"entity": "notification", "limit": 100, **query}.items()
    )
    r = await admin.get(f"{AUDIT}?{params}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def notice_total(admin, **query) -> int:
    params = "&".join(
        f"{k}={v}" for k, v in {"entity": "notification", "limit": 1, **query}.items()
    )
    return (await admin.get(f"{AUDIT}?{params}")).json()["total"]


async def operation_with_title(api, title: str) -> dict:
    r = await api.post(
        f"{API}/tickets/",
        json={"type_code": "operation", "title": title, "payload": dict(OPERATION)},
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestCreationIsAudited:
    async def test_a_notice_leaves_a_create_event(self, admin):
        before = await notice_total(admin, action="create")
        op = await operation_with_title(admin, "Vuelo con aviso")
        events = await notice_events(admin, action="create")
        assert await notice_total(admin, action="create") == before + 1
        newest = events[0]
        assert newest["entity_label"] == "operation_created"
        assert op["code"] in newest["summary"]
        assert newest["actor_name"] == "Superusuario"
        assert newest["changes"][0]["field"] == "audience"

    async def test_title_and_body_never_reach_the_trail(self, admin, logged):
        secret = f"Destino-Reservado-{uuid.uuid4().hex[:10]}"
        op = await operation_with_title(admin, secret)
        ops, _ = await logged("operaciones@transvalor.com")
        # the notice itself does quote the title (that is its job)...
        assert any(secret in n["body"] for n in await about(ops, op))
        # ...the audit events about notices do not.
        text = str(await notice_events(admin))
        assert secret not in text

    async def test_each_new_notice_adds_exactly_one_event(self, admin):
        await operation_with_title(admin, "uno")
        count = await notice_total(admin, action="create")
        await operation_with_title(admin, "dos")
        assert await notice_total(admin, action="create") == count + 1


class TestBulkReadIsAudited:
    async def test_read_all_leaves_one_event_with_the_count(self, admin, logged):
        ops, body = await logged("operaciones@transvalor.com")
        await operation_with_title(admin, "para leer todo")
        pending = (await notices(ops))["unread"]
        assert pending >= 1
        marked = await ops.post(f"{API}/notifications/read-all/")
        assert marked.json()["marked"] == pending
        events = await notice_events(admin, action="update", actor_id=body["user"]["id"])
        assert events and f"{pending} aviso(s)" in events[0]["summary"]
        assert events[0]["changes"][0]["to"] == pending
        assert events[0]["entity_label"] == "operaciones@transvalor.com"

    async def test_read_all_with_nothing_pending_adds_no_event(self, admin, logged):
        ops, body = await logged("operaciones@transvalor.com")
        await ops.post(f"{API}/notifications/read-all/")
        count = len(await notice_events(admin, action="update", actor_id=body["user"]["id"]))
        again = await ops.post(f"{API}/notifications/read-all/")
        assert again.json()["marked"] == 0
        assert (
            len(await notice_events(admin, action="update", actor_id=body["user"]["id"])) == count
        )

    async def test_read_events_never_name_a_notice(self, admin, logged):
        ops, body = await logged("operaciones@transvalor.com")
        secret = f"Reservado-{uuid.uuid4().hex[:10]}"
        await operation_with_title(admin, secret)
        await ops.post(f"{API}/notifications/read-all/")
        assert secret not in str(await notice_events(admin, actor_id=body["user"]["id"]))

    async def test_another_user_reading_does_not_audit_for_me(self, admin, logged):
        pilot, body = await logged("piloto@transvalor.com")
        before = len(await notice_events(admin, action="update", actor_id=body["user"]["id"]))
        ops, _ = await logged("operaciones@transvalor.com")
        await ops.post(f"{API}/notifications/read-all/")
        after = len(await notice_events(admin, action="update", actor_id=body["user"]["id"]))
        assert after == before
