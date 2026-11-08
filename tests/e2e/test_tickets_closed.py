import pytest

from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    children,
    get_ticket,
    history,
    make_expense,
    make_operation,
    patch,
    post_expense,
    ticket_url,
)


async def closed_expense(api, **payload):
    op = await make_operation(api)
    exp = await make_expense(api, op, **payload)
    res = await patch(api, exp, status_code="verified")
    assert res.status_code == 200, res.text
    return op, exp


class TestClosedTicketsAreImmutable:
    async def test_the_close_answers_200(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op)
        assert (await patch(admin, exp, status_code="verified")).status_code == 200

    async def test_patch_is_409(self, admin):
        _, exp = await closed_expense(admin)
        assert (await patch(admin, exp, title="cambio")).status_code == 409

    async def test_payload_patch_is_409(self, admin):
        _, exp = await closed_expense(admin)
        assert (await patch(admin, exp, payload={"notes": "x"})).status_code == 409

    async def test_comment_is_409(self, admin):
        _, exp = await closed_expense(admin)
        assert (await patch(admin, exp, comment="tarde")).status_code == 409

    async def test_reopening_is_409(self, admin):
        _, exp = await closed_expense(admin)
        assert (await patch(admin, exp, status_code="registered")).status_code == 409

    async def test_changing_to_another_final_state_is_409(self, admin):
        _, exp = await closed_expense(admin)
        assert (await patch(admin, exp, status_code="rejected")).status_code == 409

    async def test_reassigning_is_409(self, admin):
        _, exp = await closed_expense(admin)
        r = await patch(admin, exp, assigned_user_id=None, due_at="2031-01-01T00:00:00Z")
        assert r.status_code == 409

    async def test_decision_is_409(self, admin):
        _, exp = await closed_expense(admin)
        r = await admin.post(
            ticket_url(exp, "decisions/"), json={"step_code": "verify", "decision": "done"}
        )
        assert r.status_code == 409

    async def test_authorize_is_409(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, amount=800)
        await patch(admin, exp, status_code="rejected")
        assert (await admin.post(ticket_url(exp, "authorize/"), json={})).status_code == 409

    async def test_reading_proposal_is_409(self, admin):
        _, exp = await closed_expense(admin)
        r = await admin.post(
            ticket_url(exp, "extractions/"),
            json={"fields": [{"key": "amount", "value": 5, "confidence": "high"}]},
        )
        assert r.status_code == 409

    async def test_nothing_changed_after_the_attempts(self, admin):
        _, exp = await closed_expense(admin)
        await patch(admin, exp, title="cambio", payload={"amount": 999})
        again = await get_ticket(admin, exp)
        assert again["title"] == exp["title"] and again["money"]["amount"] == 100

    async def test_updated_at_is_untouched_by_rejected_attempts(self, admin):
        _, exp = await closed_expense(admin)
        before = await get_ticket(admin, exp)
        await patch(admin, exp, title="cambio")
        assert (await get_ticket(admin, exp))["updated_at"] == before["updated_at"]

    async def test_attempt_is_recorded_as_denied(self, admin):
        _, exp = await closed_expense(admin)
        await patch(admin, exp, title="cambio")
        events = await history(admin, exp)
        denied = [e for e in events if e["action"] == "denied"]
        assert denied and exp["code"] in denied[0]["summary"]

    async def test_every_blocked_attempt_is_recorded(self, admin):
        _, exp = await closed_expense(admin)
        for _ in range(3):
            await patch(admin, exp, title="cambio")
        assert len([e for e in await history(admin, exp) if e["action"] == "denied"]) == 3

    async def test_denied_event_names_the_actor(self, admin):
        _, exp = await closed_expense(admin)
        await patch(admin, exp, title="cambio")
        denied = next(e for e in await history(admin, exp) if e["action"] == "denied")
        me = (await admin.get(f"{API}/auth/me/")).json()["user"]
        assert denied["actor_id"] == me["id"]

    async def test_denied_shows_in_the_global_audit(self, admin):
        _, exp = await closed_expense(admin)
        await patch(admin, exp, title="cambio")
        events = (await admin.get(f"{API}/audit/?action=denied&entity=ticket&limit=100")).json()
        assert any(e["entity_id"] == exp["id"] for e in events["data"])

    async def test_not_even_decide_any_can_edit(self, ops):
        _, exp = await closed_expense(ops)
        assert (await patch(ops, exp, title="cambio")).status_code == 409

    async def test_pilot_gets_409_too(self, pilot, admin):
        _, exp = await closed_expense(admin)
        assert (await patch(pilot, exp, title="cambio")).status_code == 409

    async def test_a_final_state_that_does_not_count_is_also_locked(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op)
        await patch(admin, exp, status_code="rejected")
        assert (await patch(admin, exp, title="cambio")).status_code == 409

    async def test_open_tickets_stay_editable(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        assert (await patch(admin, exp, title="ok")).status_code == 200

    async def test_a_sibling_is_not_locked_by_a_closed_one(self, admin):
        op = await make_operation(admin)
        closed = await make_expense(admin, op)
        sibling = await make_expense(admin, op)
        await patch(admin, closed, status_code="verified")
        assert (await patch(admin, sibling, title="ok")).status_code == 200

    async def test_a_closed_ticket_can_still_be_read(self, admin):
        _, exp = await closed_expense(admin)
        assert (await get_ticket(admin, exp))["status_is_final"] is True
        assert (await admin.get(ticket_url(exp, "history/"))).status_code == 200

    async def test_close_stamps_closed_at_once(self, admin):
        _, exp = await closed_expense(admin)
        first = (await get_ticket(admin, exp))["closed_at"]
        await patch(admin, exp, title="cambio")
        assert (await get_ticket(admin, exp))["closed_at"] == first

    async def test_cannot_add_work_to_a_closed_parent(self, admin):
        op = await make_operation(admin)
        assert (await patch(admin, op, status_code="cancelled")).status_code == 200
        assert (await post_expense(admin, op)).status_code == 409

    async def test_denied_event_is_kept_even_though_the_request_failed(self, admin):
        _, exp = await closed_expense(admin)
        r = await patch(admin, exp, title="cambio")
        assert r.status_code == 409
        assert any(e["action"] == "denied" for e in await history(admin, exp))

    @pytest.mark.parametrize("target", ["cancelled"])
    async def test_a_cancelled_operation_is_locked(self, admin, target):
        op = await make_operation(admin)
        await patch(admin, op, status_code=target)
        assert (await patch(admin, op, title="cambio")).status_code == 409
        kids = await children(admin, op)
        assert kids, "the technical report stays attached"
