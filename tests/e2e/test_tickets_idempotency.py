import asyncio

import pytest

from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    EXPENSE,
    OPERATION,
    TICKETS,
    children,
    get_ticket,
    make_operation,
    new_key,
    ticket_url,
)


def header(key):
    return {"Idempotency-Key": key}


def expense_body(parent, amount=100, **extra):
    return {
        "type_code": "expense",
        "title": "g",
        "parent_id": parent["id"],
        "payload": {**EXPENSE, "amount": amount},
        **extra,
    }


async def count_expenses(api, op):
    return len(await children(api, op, type_code="expense"))


class TestReplay:
    async def test_same_key_same_body_creates_once_and_answers_the_same(self, admin):
        op = await make_operation(admin)
        key = new_key()
        a = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        b = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        assert a.status_code == b.status_code == 201
        assert a.json()["id"] == b.json()["id"] and a.json()["code"] == b.json()["code"]
        assert await count_expenses(admin, op) == 1

    async def test_the_replay_is_byte_identical(self, admin):
        op = await make_operation(admin)
        key = new_key()
        a = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        b = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        assert a.content == b.content

    async def test_a_replay_is_flagged(self, admin):
        op = await make_operation(admin)
        key = new_key()
        a = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        b = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        assert "idempotent-replay" not in a.headers and b.headers["idempotent-replay"] == "true"

    async def test_key_order_in_the_body_does_not_matter(self, admin):
        op = await make_operation(admin)
        key = new_key()
        first = {"type_code": "expense", "parent_id": op["id"], "payload": EXPENSE}
        second = {
            "payload": dict(reversed(list(EXPENSE.items()))),
            "parent_id": op["id"],
            "type_code": "expense",
        }
        a = await admin.post(TICKETS, json=first, headers=header(key))
        b = await admin.post(TICKETS, json=second, headers=header(key))
        assert b.status_code == 201 and a.json()["id"] == b.json()["id"]

    async def test_without_a_key_two_posts_create_two(self, admin):
        op = await make_operation(admin)
        await admin.post(TICKETS, json=expense_body(op))
        await admin.post(TICKETS, json=expense_body(op))
        assert await count_expenses(admin, op) == 2

    async def test_different_keys_create_different_tickets(self, admin):
        op = await make_operation(admin)
        a = await admin.post(TICKETS, json=expense_body(op), headers=header(new_key()))
        b = await admin.post(TICKETS, json=expense_body(op), headers=header(new_key()))
        assert a.json()["id"] != b.json()["id"]

    async def test_patch_is_idempotent_too(self, admin):
        op = await make_operation(admin)
        exp = (await admin.post(TICKETS, json=expense_body(op))).json()
        key = new_key()
        a = await admin.patch(ticket_url(exp), json={"comment": "una vez"}, headers=header(key))
        b = await admin.patch(ticket_url(exp), json={"comment": "una vez"}, headers=header(key))
        assert a.status_code == b.status_code == 200
        assert len((await get_ticket(admin, exp))["comments"]) == 1

    async def test_decisions_are_idempotent(self, admin):
        op = await make_operation(admin)
        key = new_key()
        body = {"step_code": "planning", "decision": "done"}
        a = await admin.post(ticket_url(op, "decisions/"), json=body, headers=header(key))
        b = await admin.post(ticket_url(op, "decisions/"), json=body, headers=header(key))
        assert a.status_code == b.status_code == 201
        assert len((await get_ticket(admin, op))["decisions"]) == 1

    async def test_a_replayed_creation_audits_once(self, admin):
        op = await make_operation(admin)
        key = new_key()
        a = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        events = (await admin.get(f"{API}/audit/?entity=ticket&entity_id={a.json()['id']}")).json()
        assert len([e for e in events["data"] if e["action"] == "create"]) == 1

    async def test_the_stored_answer_survives_later_changes(self, admin):
        op = await make_operation(admin)
        key = new_key()
        a = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        await admin.patch(ticket_url(a.json()), json={"title": "cambiado"})
        b = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        assert b.json()["title"] == "g"


class TestConflicts:
    async def test_same_key_other_body_is_422_and_creates_nothing(self, admin):
        op = await make_operation(admin)
        key = new_key()
        await admin.post(TICKETS, json=expense_body(op, 100), headers=header(key))
        r = await admin.post(TICKETS, json=expense_body(op, 999), headers=header(key))
        assert r.status_code == 422 and r.json()["codigo"] == "idempotencia"
        assert await count_expenses(admin, op) == 1

    async def test_the_conflict_message_is_in_spanish_and_generic(self, admin):
        op = await make_operation(admin)
        key = new_key()
        await admin.post(TICKETS, json=expense_body(op, 1), headers=header(key))
        r = await admin.post(TICKETS, json=expense_body(op, 2), headers=header(key))
        assert "idempotencia" in r.json()["detail"].lower()
        assert "Traceback" not in r.text

    async def test_the_key_is_scoped_to_the_path(self, admin):
        op = await make_operation(admin)
        exp1 = (await admin.post(TICKETS, json=expense_body(op))).json()
        exp2 = (await admin.post(TICKETS, json=expense_body(op))).json()
        key = new_key()
        a = await admin.patch(ticket_url(exp1), json={"title": "uno"}, headers=header(key))
        b = await admin.patch(ticket_url(exp2), json={"title": "uno"}, headers=header(key))
        assert a.status_code == b.status_code == 200
        assert (await get_ticket(admin, exp2))["title"] == "uno"

    async def test_another_user_never_receives_my_answer(self, admin, ops):
        op = await make_operation(admin)
        key = new_key()
        mine = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        theirs = await ops.post(TICKETS, json=expense_body(op), headers=header(key))
        assert theirs.status_code == 201
        assert theirs.json()["id"] != mine.json()["id"]
        assert theirs.json()["created_by"] != mine.json()["created_by"]

    async def test_a_rejection_is_not_remembered(self, admin):
        op = await make_operation(admin)
        key = new_key()
        bad = await admin.post(
            TICKETS,
            json={"type_code": "expense", "parent_id": op["id"], "payload": {}},
            headers=header(key),
        )
        assert bad.status_code == 422
        good = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        assert good.status_code == 201

    async def test_a_forbidden_answer_is_not_remembered(self, pilot, admin):
        op = await make_operation(admin)
        key = new_key()
        first = await pilot.post(ticket_url(op, "authorize/"), json={}, headers=header(key))
        second = await pilot.post(ticket_url(op, "authorize/"), json={}, headers=header(key))
        assert first.status_code == second.status_code == 403
        assert "idempotent-replay" not in second.headers

    async def test_a_conflict_409_on_a_closed_ticket_is_not_remembered(self, admin):
        op = await make_operation(admin)
        exp = (await admin.post(TICKETS, json=expense_body(op))).json()
        await admin.patch(ticket_url(exp), json={"status_code": "rejected"})
        key = new_key()
        r = await admin.patch(ticket_url(exp), json={"title": "x"}, headers=header(key))
        assert r.status_code == 409
        again = await admin.patch(ticket_url(exp), json={"title": "x"}, headers=header(key))
        assert again.status_code == 409 and "idempotent-replay" not in again.headers

    async def test_concurrent_identical_requests_create_one(self, admin):
        op = await make_operation(admin)
        key = new_key()
        results = await asyncio.gather(
            *(admin.post(TICKETS, json=expense_body(op), headers=header(key)) for _ in range(6))
        )
        assert await count_expenses(admin, op) == 1
        created = [r for r in results if r.status_code == 201]
        assert created
        assert len({r.json()["id"] for r in created}) == 1
        assert {r.status_code for r in results} <= {201, 409}


class TestKeyFormat:
    @pytest.mark.parametrize("key", ["a b", "x" * 200, "k;drop", "-leading-dash"])
    async def test_malformed_keys_are_rejected(self, admin, key):
        op = await make_operation(admin)
        r = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        assert r.status_code == 422 and r.json()["codigo"] == "idempotencia"

    async def test_a_rejected_key_creates_nothing(self, admin):
        op = await make_operation(admin)
        await admin.post(TICKETS, json=expense_body(op), headers=header("a b"))
        assert await count_expenses(admin, op) == 0

    async def test_short_keys_are_fine(self, admin):
        op = await make_operation(admin)
        key = f"k-{new_key()[-6:]}"
        r = await admin.post(TICKETS, json=expense_body(op), headers=header(key))
        assert r.status_code == 201

    async def test_unauthenticated_requests_with_a_key_get_401(self, anon):
        r = await anon.post(TICKETS, json={}, headers=header(new_key()))
        assert r.status_code == 401

    async def test_the_header_is_ignored_on_reads(self, admin):
        key = new_key()
        a = await admin.get(f"{TICKETS}?limit=1", headers=header(key))
        b = await admin.get(f"{TICKETS}?limit=1", headers=header(key))
        assert a.status_code == b.status_code == 200 and "idempotent-replay" not in b.headers

    async def test_routes_outside_the_ticket_area_ignore_the_key(self, admin, uniq):
        key = new_key()
        body = {"name": f"Rol {uniq()}", "permissions": []}
        a = await admin.post(f"{API}/roles/", json=body, headers=header(key))
        b = await admin.post(
            f"{API}/roles/", json={**body, "name": f"Rol {uniq()}"}, headers=header(key)
        )
        assert a.status_code == b.status_code == 201 and a.json()["id"] != b.json()["id"]

    async def test_cors_allows_the_header(self, anon):
        r = await anon.request(
            "OPTIONS",
            TICKETS,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "idempotency-key",
            },
        )
        assert r.status_code == 200
        assert "idempotency-key" in r.headers["access-control-allow-headers"].lower()


class TestOtherAreas:
    async def test_rule_creation_is_idempotent(self, admin, uniq):
        key = new_key()
        body = {
            "name": f"Regla {uniq()} única",
            "type_code": "expense",
            "field": "amount_base",
            "operator": "gt",
            "value": 99999,
            "action": "warn",
            "message": "Mensaje de la regla",
        }
        a = await admin.post(f"{API}/rules/", json=body, headers=header(key))
        b = await admin.post(f"{API}/rules/", json=body, headers=header(key))
        assert a.json()["id"] == b.json()["id"]
        await admin.delete(f"{API}/rules/{a.json()['id']}/")
