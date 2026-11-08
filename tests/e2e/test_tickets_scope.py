import pytest

from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    EXPENSE,
    OPERATION,
    TICKETS,
    field,
    get_ticket,
    history,
    make_expense,
    make_operation,
    patch,
    status,
    ticket_url,
)

ZERO = "00000000-0000-0000-0000-000000000000"


async def only(new_account, *permissions):
    api, user, _ = await new_account(list(permissions))
    return api


class TestScopeByType:
    async def test_global_view_sees_every_type(self, admin, pilot):
        op = await make_operation(admin)
        rows = (await pilot.get(f"{TICKETS}?parent_id={op['id']}&limit=100")).json()["data"]
        assert rows

    async def test_per_type_viewer_lists_only_that_type(self, new_account, admin):
        api = await only(new_account, "tickets.view.expense")
        op = await make_operation(admin)
        exp = await make_expense(admin, op)
        rows = (await api.get(f"{TICKETS}?limit=100")).json()["data"]
        assert exp["id"] in {t["id"] for t in rows}
        assert {t["type_code"] for t in rows} == {"expense"}

    async def test_total_counts_only_what_is_visible(self, new_account, admin):
        api = await only(new_account, "tickets.view.expense")
        await make_operation(admin)
        everything = (await admin.get(f"{TICKETS}?limit=1")).json()["total"]
        mine = (await api.get(f"{TICKETS}?limit=1")).json()["total"]
        assert mine < everything

    async def test_filtering_for_a_hidden_type_yields_nothing(self, new_account, admin):
        api = await only(new_account, "tickets.view.expense")
        await make_operation(admin)
        rows = (await api.get(f"{TICKETS}?type_code=operation")).json()
        assert rows["data"] == [] and rows["total"] == 0

    async def test_search_cannot_reach_hidden_types(self, new_account, admin):
        api = await only(new_account, "tickets.view.expense")
        op = await make_operation(admin)
        rows = (await api.get(f"{TICKETS}?q={op['code']}&limit=100")).json()["data"]
        assert op["id"] not in {t["id"] for t in rows}

    async def test_children_listing_of_a_hidden_parent_leaks_only_visible_types(
        self, new_account, admin
    ):
        api = await only(new_account, "tickets.view.expense")
        op = await make_operation(admin)
        await make_expense(admin, op)
        rows = (await api.get(f"{TICKETS}?parent_id={op['id']}&limit=100")).json()["data"]
        assert {t["type_code"] for t in rows} <= {"expense"}

    async def test_two_per_type_viewers_see_the_union(self, new_account, admin):
        api = await only(new_account, "tickets.view.expense", "tickets.view.technical_report")
        op = await make_operation(admin)
        rows = (await api.get(f"{TICKETS}?parent_id={op['id']}&limit=100")).json()["data"]
        assert {t["type_code"] for t in rows} == {"technical_report"}
        await make_expense(admin, op)
        rows = (await api.get(f"{TICKETS}?parent_id={op['id']}&limit=100")).json()["data"]
        assert {t["type_code"] for t in rows} == {"technical_report", "expense"}

    async def test_user_without_ticket_permissions_is_403(self, new_account):
        api = await only(new_account, "catalogs.view")
        assert (await api.get(TICKETS)).status_code == 403

    async def test_total_for_a_user_whose_type_has_no_tickets_is_empty(self, new_account):
        api = await only(new_account, "tickets.view.inac_permit")
        rows = (await api.get(TICKETS)).json()
        assert rows["total"] >= 0 and {t["type_code"] for t in rows["data"]} <= {"inac_permit"}


class TestIdor:
    """A ticket outside the caller's scope answers 404 everywhere: never 403, never data."""

    async def hidden(self, new_account, admin):
        api = await only(
            new_account,
            "tickets.view.expense",
            "tickets.edit",
            "tickets.create",
            "tickets.decide_any",
        )
        op = await make_operation(admin)
        return api, op

    async def test_retrieve_is_404(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        assert (await api.get(ticket_url(op))).status_code == 404

    async def test_update_is_404_and_changes_nothing(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        assert (await patch(api, op, title="hack")).status_code == 404
        assert (await get_ticket(admin, op))["title"] == op["title"]

    async def test_status_change_is_404(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        assert (await patch(api, op, status_code="cancelled")).status_code == 404
        assert (await get_ticket(admin, op))["status_code"] == "planned"

    async def test_decision_is_404(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        r = await api.post(
            ticket_url(op, "decisions/"), json={"step_code": "planning", "decision": "done"}
        )
        assert r.status_code == 404
        assert (await get_ticket(admin, op))["decisions"] == []

    async def test_history_is_404(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        assert (await api.get(ticket_url(op, "history/"))).status_code == 404

    async def test_authorize_is_404(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        assert (await api.post(ticket_url(op, "authorize/"), json={})).status_code == 404

    async def test_readings_are_404(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        assert (await api.get(ticket_url(op, "extractions/"))).status_code == 404
        r = await api.post(
            ticket_url(op, "extractions/"),
            json={"fields": [{"key": "origin", "value": "CCS", "confidence": "high"}]},
        )
        assert r.status_code == 404

    async def test_validating_a_reading_is_404(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        r = await api.post(ticket_url(op, f"extractions/{ZERO}/validate/"), json={})
        assert r.status_code == 404

    async def test_adding_a_child_to_a_hidden_parent_is_404(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        r = await api.post(
            TICKETS,
            json={"type_code": "expense", "parent_id": op["id"], "payload": EXPENSE},
        )
        assert r.status_code == 404

    async def test_hidden_and_missing_are_indistinguishable(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        hidden = await api.get(ticket_url(op))
        missing = await api.get(ticket_url(ZERO))
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()

    async def test_hidden_and_missing_updates_are_indistinguishable(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        hidden = await patch(api, op, title="x")
        missing = await patch(api, ZERO, title="x")
        assert hidden.status_code == missing.status_code and hidden.json() == missing.json()

    async def test_delete_is_not_available_at_all(self, new_account, admin):
        api, op = await self.hidden(new_account, admin)
        assert (await api.delete(ticket_url(op))).status_code == 405

    async def test_a_visible_ticket_of_the_same_user_works(self, new_account, admin):
        api = await only(new_account, "tickets.view.expense", "tickets.edit")
        exp = await make_expense(admin, await make_operation(admin))
        assert (await patch(api, exp, title="mío")).status_code == 200

    async def test_foreign_extraction_of_a_visible_ticket_is_404(self, accounting, admin):
        a = await make_expense(admin, await make_operation(admin))
        b = await make_expense(admin, await make_operation(admin))
        r = await accounting.post(
            ticket_url(a, "extractions/"),
            json={"fields": [{"key": "amount", "value": 5, "confidence": "high"}]},
        )
        out = await accounting.post(
            ticket_url(b, f"extractions/{r.json()['id']}/validate/"), json={}
        )
        assert out.status_code == 404

    async def test_parent_cannot_be_swapped_by_patch(self, admin):
        op1, op2 = await make_operation(admin), await make_operation(admin)
        exp = await make_expense(admin, op1)
        await admin.patch(ticket_url(exp), json={"parent_id": op2["id"]})
        assert (await get_ticket(admin, exp))["parent_id"] == op1["id"]


class TestEditAndCreatePermissions:
    async def test_create_per_type(self, new_account, admin):
        api = await only(new_account, "tickets.create.hangar_incident", "tickets.view")
        ok = await api.post(
            TICKETS, json={"type_code": "hangar_incident", "payload": {"description": "golpe"}}
        )
        assert ok.status_code == 201
        no = await api.post(TICKETS, json={"type_code": "operation", "payload": OPERATION})
        assert no.status_code == 403

    async def test_create_without_any_create_permission_is_403(self, maintenance):
        r = await maintenance.post(TICKETS, json={"type_code": "operation", "payload": OPERATION})
        assert r.status_code == 403

    async def test_global_create_covers_every_type(self, pilot):
        r = await pilot.post(TICKETS, json={"type_code": "operation", "payload": OPERATION})
        assert r.status_code == 201

    async def test_edit_per_type(self, new_account, admin):
        api = await only(new_account, "tickets.view", "tickets.edit.expense")
        op = await make_operation(admin)
        exp = await make_expense(admin, op)
        assert (await patch(api, exp, title="ok")).status_code == 200
        assert (await patch(api, op, title="no")).status_code == 403

    async def test_view_only_user_cannot_edit(self, new_account, admin):
        api = await only(new_account, "tickets.view")
        exp = await make_expense(admin, await make_operation(admin))
        assert (await patch(api, exp, title="x")).status_code == 403

    async def test_view_only_user_cannot_decide(self, new_account, admin):
        api = await only(new_account, "tickets.view")
        op = await make_operation(admin)
        r = await api.post(
            ticket_url(op, "decisions/"), json={"step_code": "planning", "decision": "done"}
        )
        assert r.status_code == 403

    async def test_edit_without_view_of_that_type_is_404_not_403(self, new_account, admin):
        api = await only(new_account, "tickets.edit")
        op = await make_operation(admin)
        assert (await patch(api, op, title="x")).status_code == 404

    async def test_maintenance_cannot_create_but_can_edit(self, maintenance, admin):
        op = await make_operation(admin)
        report = next(
            t
            for t in (await admin.get(f"{TICKETS}?parent_id={op['id']}")).json()["data"]
            if t["type_code"] == "technical_report"
        )
        assert (await patch(maintenance, report, payload={"observations": "ok"})).status_code == 200


class TestFieldPermissions:
    async def test_edit_permission_on_a_field_is_enforced_in_the_payload(self, pilot, admin):
        op = await make_operation(admin)
        report = next(
            t
            for t in (await admin.get(f"{TICKETS}?parent_id={op['id']}")).json()["data"]
            if t["type_code"] == "technical_report"
        )
        r = await patch(pilot, report, payload={"corrective_action": "Cambio de filtro"})
        assert r.status_code == 403
        assert (await get_ticket(admin, report))["payload"].get("corrective_action") is None

    async def test_the_holder_of_the_field_permission_can_write_it(self, admin):
        op = await make_operation(admin)
        report = next(
            t
            for t in (await admin.get(f"{TICKETS}?parent_id={op['id']}")).json()["data"]
            if t["type_code"] == "technical_report"
        )
        r = await patch(admin, report, payload={"corrective_action": "Cambio de filtro"})
        assert r.status_code == 200

    async def test_a_forbidden_field_rejects_the_whole_request(self, pilot, admin):
        op = await make_operation(admin)
        report = next(
            t
            for t in (await admin.get(f"{TICKETS}?parent_id={op['id']}")).json()["data"]
            if t["type_code"] == "technical_report"
        )
        r = await patch(
            pilot,
            report,
            title="Nuevo",
            payload={"observations": "x", "director_authorization": "yo"},
        )
        assert r.status_code == 403
        again = await get_ticket(admin, report)
        assert again["title"] == report["title"] and not again["payload"].get("observations")

    async def test_decide_any_field_is_only_for_management(self, ops, pilot, admin):
        op = await make_operation(admin)
        report = next(
            t
            for t in (await admin.get(f"{TICKETS}?parent_id={op['id']}")).json()["data"]
            if t["type_code"] == "technical_report"
        )
        assert (
            await patch(pilot, report, payload={"director_authorization": "ok"})
        ).status_code == 403
        assert (
            await patch(ops, report, payload={"director_authorization": "ok"})
        ).status_code == 200

    async def test_fields_the_user_may_not_view_are_hidden(self, admin, pilot, uniq):
        code = f"vista_{uniq()[:6]}"
        t = await admin.post(
            f"{API}/ticket-types/",
            json={
                "name": f"Con secreto {code}",
                "code": code,
                "fields": [
                    field("open_note", "Nota"),
                    field("secret", "Secreto", view_permission="audit.view"),
                ],
                "statuses": [
                    status("a", is_initial=True),
                    status("z", is_final=True),
                ],
            },
        )
        assert t.status_code == 201, t.text
        made = await admin.post(
            TICKETS,
            json={"type_code": code, "payload": {"open_note": "hola", "secret": "clave"}},
        )
        assert made.status_code == 201, made.text
        assert made.json()["payload"]["secret"] == "clave"
        seen = await pilot.get(ticket_url(made.json()))
        assert seen.json()["payload"] == {"open_note": "hola"}
        listed = (await pilot.get(f"{TICKETS}?type_code={code}")).json()["data"][0]
        assert "secret" not in listed["payload"]

    async def test_the_pilot_cannot_read_the_global_audit(self, pilot):
        assert (await pilot.get(f"{API}/audit/")).status_code == 403


class TestDecisions:
    async def test_the_step_role_may_decide(self, ops, admin):
        op = await make_operation(admin)
        r = await ops.post(
            ticket_url(op, "decisions/"),
            json={"step_code": "planning", "decision": "done", "observation": "ok"},
        )
        assert r.status_code == 201
        d = r.json()["decisions"][0]
        assert d["step_code"] == "planning" and d["decision"] == "done" and d["observation"] == "ok"
        assert d["decided_by"] and d["decided_at"]

    async def test_another_role_may_not(self, pilot, admin):
        op = await make_operation(admin)
        r = await pilot.post(
            ticket_url(op, "decisions/"), json={"step_code": "planning", "decision": "done"}
        )
        assert r.status_code == 403
        assert (await get_ticket(admin, op))["decisions"] == []

    async def test_the_pilot_decides_their_own_step(self, pilot, admin):
        op = await make_operation(admin)
        r = await pilot.post(
            ticket_url(op, "decisions/"), json={"step_code": "requirements", "decision": "done"}
        )
        assert r.status_code == 201

    async def test_decide_any_overrides_the_role(self, ops, admin):
        op = await make_operation(admin)
        r = await ops.post(
            ticket_url(op, "decisions/"), json={"step_code": "technical", "decision": "done"}
        )
        assert r.status_code == 201

    async def test_unknown_step_is_422(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            ticket_url(op, "decisions/"), json={"step_code": "ghost", "decision": "done"}
        )
        assert r.status_code == 422

    @pytest.mark.parametrize("decision", ["maybe", "", None, 3])
    async def test_invalid_decision_is_422(self, admin, decision):
        op = await make_operation(admin)
        r = await admin.post(
            ticket_url(op, "decisions/"), json={"step_code": "planning", "decision": decision}
        )
        assert r.status_code == 422

    @pytest.mark.parametrize("decision", ["done", "rejected", "not_applicable"])
    async def test_every_valid_decision(self, admin, decision):
        op = await make_operation(admin)
        r = await admin.post(
            ticket_url(op, "decisions/"), json={"step_code": "planning", "decision": decision}
        )
        assert r.status_code == 201 and r.json()["decisions"][0]["decision"] == decision

    async def test_repeating_a_step_replaces_the_decision(self, admin):
        op = await make_operation(admin)
        url = ticket_url(op, "decisions/")
        await admin.post(url, json={"step_code": "planning", "decision": "rejected"})
        r = await admin.post(url, json={"step_code": "planning", "decision": "done"})
        decisions = r.json()["decisions"]
        assert len(decisions) == 1 and decisions[0]["decision"] == "done"

    async def test_the_replaced_decision_survives_in_the_audit(self, admin):
        op = await make_operation(admin)
        url = ticket_url(op, "decisions/")
        await admin.post(url, json={"step_code": "planning", "decision": "rejected"})
        await admin.post(url, json={"step_code": "planning", "decision": "done"})
        events = [e for e in await history(admin, op) if e["action"] == "decision"]
        assert len(events) == 2
        newest = events[0]
        change = next(c for c in newest["changes"] if c["field"] == "decision")
        assert change["from"] == "Rechazado" and change["to"] == "Cumplido"
        assert "Rectificó" in newest["summary"]

    async def test_first_decision_moves_the_ticket_in_progress(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            ticket_url(op, "decisions/"), json={"step_code": "planning", "decision": "done"}
        )
        assert r.json()["status_code"] == "in_progress"

    async def test_a_type_without_steps_rejects_decisions(self, admin):
        r = await admin.post(
            TICKETS, json={"type_code": "hangar_incident", "payload": {"description": "x"}}
        )
        out = await admin.post(
            ticket_url(r.json(), "decisions/"), json={"step_code": "nope", "decision": "done"}
        )
        assert out.status_code == 422

    async def test_observation_has_a_limit(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            ticket_url(op, "decisions/"),
            json={"step_code": "planning", "decision": "done", "observation": "x" * 5000},
        )
        assert r.status_code == 422

    async def test_decision_is_audited_with_the_step_name(self, admin):
        op = await make_operation(admin)
        await admin.post(
            ticket_url(op, "decisions/"), json={"step_code": "planning", "decision": "done"}
        )
        event = next(e for e in await history(admin, op) if e["action"] == "decision")
        assert "Planificación" in event["summary"]

    async def test_forged_decider_in_the_body_is_ignored(self, ops, admin):
        op = await make_operation(admin)
        r = await ops.post(
            ticket_url(op, "decisions/"),
            json={
                "step_code": "planning",
                "decision": "done",
                "decided_by": "Gerente General",
                "decided_at": "2020-01-01T00:00:00Z",
            },
        )
        assert r.status_code in (201, 422)
        if r.status_code == 201:
            d = r.json()["decisions"][0]
            assert d["decided_by"] != "Gerente General" and not d["decided_at"].startswith("2020")


class TestHistory:
    async def test_newest_first_and_only_this_ticket(self, admin):
        op = await make_operation(admin)
        other = await make_operation(admin)
        await patch(admin, op, title="Uno")
        await patch(admin, op, title="Dos")
        events = await history(admin, op)
        assert {e["entity_id"] for e in events} == {op["id"]}
        assert other["id"] not in {e["entity_id"] for e in events}
        times = [e["at"] for e in events]
        assert times == sorted(times, reverse=True)

    async def test_kinds_of_events(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op, amount=900)
        await patch(admin, exp, title="Otro", comment="nota")
        await patch(admin, exp, status_code="rejected")
        actions = {e["action"] for e in await history(admin, exp)}
        assert {"create", "update", "comment", "status_change"} <= actions

    async def test_pagination(self, admin):
        op = await make_operation(admin)
        for i in range(4):
            await patch(admin, op, title=f"T{i}")
        r = await admin.get(ticket_url(op, "history/?limit=2&page=2"))
        body = r.json()
        assert body["page"] == 2 and len(body["data"]) == 2 and body["total"] >= 5

    async def test_a_pilot_reads_the_history_of_a_visible_ticket(self, pilot, admin):
        op = await make_operation(admin)
        assert (await pilot.get(ticket_url(op, "history/"))).status_code == 200

    async def test_unknown_ticket_is_404(self, admin):
        assert (await admin.get(ticket_url(ZERO, "history/"))).status_code == 404

    async def test_actor_is_the_authenticated_user(self, ops, admin):
        op = await make_operation(admin)
        await patch(ops, op, title="Del jefe")
        me = (await ops.get(f"{API}/auth/me/")).json()["user"]
        update = next(e for e in await history(admin, op) if e["action"] == "update")
        assert update["actor_id"] == me["id"]

    async def test_events_carry_no_internals(self, admin):
        op = await make_operation(admin)
        text = str(await history(admin, op))
        assert "Traceback" not in text and "password" not in text.lower()

    async def test_history_of_a_hidden_type_is_404(self, new_account, admin):
        api = await only(new_account, "tickets.view.expense")
        op = await make_operation(admin)
        assert (await api.get(ticket_url(op, "history/"))).status_code == 404


class TestAccessControlBasics:
    @pytest.mark.parametrize(
        ("method", "suffix"),
        [
            ("get", ""),
            ("post", ""),
            ("get", "{id}/"),
            ("patch", "{id}/"),
            ("get", "{id}/history/"),
            ("post", "{id}/decisions/"),
            ("post", "{id}/authorize/"),
            ("get", "{id}/extractions/"),
        ],
    )
    async def test_anonymous_is_401(self, anon, method, suffix):
        url = TICKETS + suffix.replace("{id}", ZERO)
        assert (await anon.request(method.upper(), url, json={})).status_code == 401

    async def test_revoked_token_cannot_read(self, anon):
        assert (
            await anon.get(TICKETS, headers={"Authorization": "Bearer nope"})
        ).status_code == 401

    async def test_errors_are_generic(self, admin):
        r = await admin.get(f"{TICKETS}?from=ayer")
        assert "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()
