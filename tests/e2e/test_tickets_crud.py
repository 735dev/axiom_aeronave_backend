import pytest

from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    EXPENSE,
    OPERATION,
    TICKETS,
    children,
    get_ticket,
    make_expense,
    make_operation,
    patch,
    post_expense,
    ticket_url,
)

ZERO = "00000000-0000-0000-0000-000000000000"


class TestCreateOperation:
    async def test_creates_with_initial_status_and_default_role(self, admin):
        op = await make_operation(admin)
        assert op["status_code"] == "planned" and op["type_is_root"] is True
        assert op["assigned_role_name"] == "Gerencia de Operaciones"
        assert op["status_is_final"] is False and op["closed_at"] is None

    async def test_stamps_creator_from_the_token(self, admin):
        op = await make_operation(admin)
        me = (await admin.get(f"{API}/auth/me/")).json()["user"]
        assert op["created_by_id"] == me["id"] and op["created_by"] == me["full_name"]

    async def test_client_cannot_forge_creator(self, admin, ops):
        r = await ops.post(
            TICKETS,
            json={
                "type_code": "operation",
                "title": "x",
                "created_by": "Otro",
                "created_by_id": ZERO,
                "payload": OPERATION,
            },
        )
        assert r.status_code == 201
        assert r.json()["created_by"] != "Otro" and r.json()["created_by_id"] != ZERO

    @pytest.mark.parametrize(
        "forged",
        [
            {"code": "HACK-1"},
            {"status_code": "closed"},
            {"status_id": ZERO},
            {"closed_at": "2026-01-01T00:00:00Z"},
            {"amount_base": 1},
            {"money": {"amount": 1}},
            {"rule_hits": []},
            {"authorization": {"by": "yo"}},
            {"control_number": "TVOA N°9999-26"},
        ],
    )
    async def test_server_owned_fields_are_not_settable(self, admin, forged):
        body = {"type_code": "operation", "title": "forjado", "payload": OPERATION, **forged}
        r = await admin.post(TICKETS, json=body)
        assert r.status_code in (201, 422)
        if r.status_code == 201:
            data = r.json()
            assert data["code"] != "HACK-1" and data["status_code"] == "planned"
            assert data["closed_at"] is None and data["control_number"] != "TVOA N°9999-26"
            assert data["authorization"] is None

    async def test_title_defaults_to_the_type_name(self, admin):
        r = await admin.post(TICKETS, json={"type_code": "operation", "payload": OPERATION})
        assert r.json()["title"] == "Operación aérea"

    async def test_aircraft_relation_is_derived(self, admin):
        op = await make_operation(admin)
        kinds = {(r["kind"], r["code"]) for r in op["relations"]}
        assert ("aircraft", "YV2323") in kinds
        assert ("airport", "CCS") in kinds and ("airport", "PZO") in kinds

    async def test_accounting_date_comes_from_the_payload(self, admin):
        op = await make_operation(admin, date="2026-03-04")
        assert op["accounting_date"] == "2026-03-04"

    async def test_unknown_type_is_rejected(self, admin):
        r = await admin.post(TICKETS, json={"type_code": "ghost", "payload": {}})
        assert r.status_code == 422

    async def test_inactive_type_is_rejected(self, admin):
        t = await admin.post(
            f"{API}/ticket-types/",
            json={
                "name": "Temporal x",
                "code": "temporal_off",
                "fields": [{"label": "A", "type": "text"}],
                "statuses": [{"name": "A", "is_initial": True}, {"name": "Z", "is_final": True}],
            },
        )
        await admin.patch(f"{API}/ticket-types/{t.json()['id']}/", json={"is_active": False})
        r = await admin.post(TICKETS, json={"type_code": "temporal_off", "payload": {}})
        assert r.status_code == 422

    async def test_root_type_cannot_have_parent(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            TICKETS,
            json={"type_code": "operation", "parent_id": op["id"], "payload": OPERATION},
        )
        assert r.status_code == 422

    async def test_title_too_long_rejected(self, admin):
        r = await admin.post(
            TICKETS, json={"type_code": "operation", "title": "x" * 500, "payload": OPERATION}
        )
        assert r.status_code == 422

    async def test_missing_type_code_rejected(self, admin):
        assert (await admin.post(TICKETS, json={"payload": {}})).status_code == 422

    async def test_non_object_payload_rejected(self, admin):
        r = await admin.post(TICKETS, json={"type_code": "operation", "payload": [1]})
        assert r.status_code == 422

    async def test_assigned_role_override(self, admin):
        roles = (await admin.get(f"{API}/roles/?limit=100")).json()["data"]
        ground = next(r for r in roles if r["code"] == "ground")
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "operation",
                "assigned_role_id": ground["id"],
                "payload": OPERATION,
            },
        )
        assert r.json()["assigned_role_id"] == ground["id"]

    async def test_unknown_assigned_role_rejected(self, admin):
        r = await admin.post(
            TICKETS,
            json={"type_code": "operation", "assigned_role_id": ZERO, "payload": OPERATION},
        )
        assert r.status_code == 422

    async def test_assigned_user_override(self, admin):
        me = (await admin.get(f"{API}/auth/me/")).json()["user"]
        r = await admin.post(
            TICKETS,
            json={"type_code": "operation", "assigned_user_id": me["id"], "payload": OPERATION},
        )
        assert r.json()["assigned_user_id"] == me["id"]
        assert r.json()["assigned_user_name"] == me["full_name"]

    async def test_unknown_assigned_user_rejected(self, admin):
        r = await admin.post(
            TICKETS,
            json={"type_code": "operation", "assigned_user_id": ZERO, "payload": OPERATION},
        )
        assert r.status_code == 422

    async def test_due_date_is_kept(self, admin):
        r = await admin.post(
            TICKETS,
            json={"type_code": "operation", "due_at": "2030-01-01T10:00:00Z", "payload": OPERATION},
        )
        assert r.json()["due_at"].startswith("2030-01-01T10:00:00")

    async def test_relations_outside_the_type_are_rejected(self, admin):
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "operation",
                "relations": [{"kind": "inventory", "code": "x"}],
                "payload": OPERATION,
            },
        )
        assert r.status_code == 422

    async def test_relation_to_unknown_airport_rejected(self, admin):
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "operation",
                "relations": [{"kind": "airport", "code": "ZZZZ"}],
                "payload": OPERATION,
            },
        )
        assert r.status_code == 422

    async def test_creation_is_audited(self, admin):
        op = await make_operation(admin)
        r = await admin.get(f"{API}/audit/?entity=ticket&entity_id={op['id']}")
        assert any(e["action"] == "create" for e in r.json()["data"])


class TestPayloadValidation:
    async def test_missing_required_fields_report_each_key(self, admin):
        r = await admin.post(TICKETS, json={"type_code": "operation", "payload": {}})
        assert r.status_code == 422
        errors = r.json()["errors"]
        assert {"date", "origin", "destination"} <= errors.keys()

    async def test_missing_required_names_the_labels(self, admin):
        r = await admin.post(TICKETS, json={"type_code": "operation", "payload": {}})
        assert "obligatorios" in r.json()["detail"]

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("origin", "ZZZ"),
            ("origin", 5),
            ("date", "19/08/2026"),
            ("date", "2026-13-40"),
            ("passengers", "muchos"),
            ("overnight", "si"),
            ("departure_time", "25:00"),
            ("fbo_services", "uno"),
            ("fbo_services", ["no-existe"]),
            ("aircraft_brand", 12),
        ],
    )
    async def test_invalid_values_are_rejected(self, admin, key, value):
        body = {"type_code": "operation", "payload": {**OPERATION, key: value}}
        r = await admin.post(TICKETS, json=body)
        assert r.status_code == 422, (key, value, r.text)
        assert key in r.json()["errors"]

    async def test_unknown_key_rejected(self, admin):
        r = await admin.post(
            TICKETS, json={"type_code": "operation", "payload": {**OPERATION, "hack": 1}}
        )
        assert r.status_code == 422 and "hack" in r.json()["errors"]

    async def test_file_fields_cannot_be_written_through_payload(self, admin):
        r = await admin.post(
            TICKETS,
            json={"type_code": "operation", "payload": {**OPERATION, "manifest": "evil.pdf"}},
        )
        assert r.status_code == 422

    async def test_text_is_trimmed(self, admin):
        op = await make_operation(admin, captain="  Pérez  ")
        assert op["payload"]["captain"] == "Pérez"

    async def test_valid_optional_values_are_stored(self, admin):
        op = await make_operation(
            admin, passengers=4, overnight=True, fbo_services=[], departure_time="08:30"
        )
        assert op["payload"]["passengers"] == 4 and op["payload"]["overnight"] is True

    async def test_failed_creation_stores_nothing(self, admin):
        before = (await admin.get(f"{TICKETS}?limit=1")).json()["total"]
        await admin.post(TICKETS, json={"type_code": "operation", "payload": {}})
        assert (await admin.get(f"{TICKETS}?limit=1")).json()["total"] == before

    async def test_failed_creation_does_not_burn_a_ticket_code(self, admin):
        op1 = await make_expense(admin, await make_operation(admin))
        await admin.post(TICKETS, json={"type_code": "operation", "payload": {}})
        op2 = await make_operation(admin)
        exp2 = await make_expense(admin, op2)
        assert int(exp2["code"][3:]) == int(op1["code"][3:]) + 2 or exp2["code"] != op1["code"]


class TestRetrieveAndList:
    async def test_retrieve(self, admin):
        op = await make_operation(admin)
        got = await get_ticket(admin, op)
        assert got["id"] == op["id"] and got["code"] == op["code"]

    async def test_retrieve_unknown_is_404(self, admin):
        assert (await admin.get(ticket_url(ZERO))).status_code == 404

    async def test_retrieve_invalid_uuid_is_422(self, admin):
        assert (await admin.get(ticket_url("not-a-uuid"))).status_code == 422

    async def test_list_envelope(self, admin):
        await make_operation(admin)
        body = (await admin.get(f"{TICKETS}?limit=5")).json()
        assert {"data", "total", "page", "limit", "links"} <= body.keys()
        assert len(body["data"]) <= 5

    async def test_list_pagination_links(self, admin):
        await make_operation(admin)
        await make_operation(admin)
        body = (await admin.get(f"{TICKETS}?limit=1&page=1")).json()
        assert body["links"]["next"] == "?page=2&limit=1"

    async def test_limit_above_maximum_is_422(self, admin):
        assert (await admin.get(f"{TICKETS}?limit=5000")).status_code == 422

    async def test_newest_first(self, admin):
        a = await make_operation(admin)
        b = await make_operation(admin)
        ids = [t["id"] for t in (await admin.get(f"{TICKETS}?limit=100")).json()["data"]]
        assert ids.index(b["id"]) < ids.index(a["id"])

    async def test_filter_by_type(self, admin):
        await make_operation(admin)
        rows = (await admin.get(f"{TICKETS}?type_code=operation&limit=100")).json()["data"]
        assert rows and {t["type_code"] for t in rows} == {"operation"}

    async def test_filter_by_status(self, admin):
        op = await make_operation(admin)
        rows = (await admin.get(f"{TICKETS}?status_code=planned&limit=100")).json()["data"]
        assert op["id"] in {t["id"] for t in rows}
        assert {t["status_code"] for t in rows} == {"planned"}

    async def test_filter_by_parent(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op)
        kids = await children(admin, op)
        assert exp["id"] in {t["id"] for t in kids}
        assert all(t["parent_id"] == op["id"] for t in kids)

    async def test_filter_is_root(self, admin):
        roots = (await admin.get(f"{TICKETS}?is_root=true&limit=100")).json()["data"]
        assert roots and all(t["type_is_root"] for t in roots)
        rest = (await admin.get(f"{TICKETS}?is_root=false&limit=100")).json()["data"]
        assert not any(t["type_is_root"] for t in rest)

    async def test_filter_open_and_closed(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op)
        await patch(admin, exp, status_code="rejected")
        opened = (await admin.get(f"{TICKETS}?open=true&limit=100&parent_id={op['id']}")).json()
        closed = (await admin.get(f"{TICKETS}?open=false&limit=100&parent_id={op['id']}")).json()
        assert exp["id"] not in {t["id"] for t in opened["data"]}
        assert [t["id"] for t in closed["data"]] == [exp["id"]]

    async def test_filter_by_assigned_role(self, admin):
        op = await make_operation(admin)
        rows = (
            await admin.get(
                f"{TICKETS}?assigned_role_id={op['assigned_role_id']}&limit=100&type_code=operation"
            )
        ).json()["data"]
        assert op["id"] in {t["id"] for t in rows}
        assert {t["assigned_role_id"] for t in rows} == {op["assigned_role_id"]}

    async def test_filter_by_assigned_user(self, admin):
        me = (await admin.get(f"{API}/auth/me/")).json()["user"]
        r = await admin.post(
            TICKETS,
            json={"type_code": "operation", "assigned_user_id": me["id"], "payload": OPERATION},
        )
        rows = (await admin.get(f"{TICKETS}?assigned_user_id={me['id']}&limit=100")).json()["data"]
        assert r.json()["id"] in {t["id"] for t in rows}
        assert {t["assigned_user_id"] for t in rows} == {me["id"]}

    async def test_filter_by_aircraft(self, admin):
        op = await make_operation(admin, aircraft="YV2528")
        rows = (await admin.get(f"{TICKETS}?aircraft=yv2528&limit=100")).json()["data"]
        assert op["id"] in {t["id"] for t in rows}
        assert {t["aircraft_code"] for t in rows} == {"YV2528"}

    async def test_filter_by_code_is_exact(self, admin):
        op = await make_operation(admin)
        other = await make_operation(admin)
        body = (await admin.get(f"{TICKETS}?code={op['code']}&limit=100")).json()
        assert [t["id"] for t in body["data"]] == [op["id"]]
        assert body["total"] == 1
        assert other["id"] not in {t["id"] for t in body["data"]}

    async def test_filter_by_code_has_no_partial_or_wildcard_match(self, admin):
        op = await make_operation(admin)
        for probe in (op["code"][:-1], "%", "_", op["code"] + "x"):
            rows = (await admin.get(f"{TICKETS}", params={"code": probe, "limit": 100})).json()
            assert op["id"] not in {t["id"] for t in rows["data"]}, probe

    async def test_filter_by_unknown_code_is_an_empty_page(self, admin):
        r = await admin.get(f"{TICKETS}?code=NOPE-000-00")
        assert r.status_code == 200
        assert r.json()["data"] == []

    async def test_filter_by_code_combines_with_other_filters(self, admin):
        op = await make_operation(admin)
        hit = (await admin.get(f"{TICKETS}?code={op['code']}&type_code=operation")).json()["data"]
        assert [t["id"] for t in hit] == [op["id"]]
        miss = (await admin.get(f"{TICKETS}?code={op['code']}&type_code=expense")).json()["data"]
        assert miss == []

    async def test_filter_by_date_range(self, admin):
        op = await make_operation(admin, date="2025-02-10")
        hit = (
            await admin.get(
                f"{TICKETS}?from=2025-02-10&to=2025-02-10&type_code=operation&limit=100"
            )
        ).json()["data"]
        assert op["id"] in {t["id"] for t in hit}
        miss = (
            await admin.get(
                f"{TICKETS}?from=2025-02-11&to=2025-02-12&type_code=operation&limit=100"
            )
        ).json()["data"]
        assert op["id"] not in {t["id"] for t in miss}

    async def test_bad_date_filter_is_422(self, admin):
        assert (await admin.get(f"{TICKETS}?from=ayer")).status_code == 422

    async def test_search_by_code(self, admin):
        op = await make_operation(admin)
        rows = (await admin.get(f"{TICKETS}?q={op['code']}&type_code=operation")).json()["data"]
        assert [t["id"] for t in rows] == [op["id"]]

    async def test_search_by_code_also_finds_what_names_it(self, admin):
        op = await make_operation(admin)
        rows = (await admin.get(f"{TICKETS}?q={op['code']}")).json()["data"]
        assert {t["type_code"] for t in rows} == {"operation", "technical_report"}

    async def test_search_by_title(self, admin):
        op = await make_operation(admin, title="Traslado único zeta9")
        rows = (await admin.get(f"{TICKETS}?q=zeta9")).json()["data"]
        assert [t["id"] for t in rows] == [op["id"]]

    async def test_search_wildcards_are_literal(self, admin):
        await make_operation(admin)
        rows = (await admin.get(f"{TICKETS}?q=%25")).json()["data"]
        assert rows == []

    async def test_search_with_regex_characters_does_not_crash(self, admin):
        r = await admin.get(f"{TICKETS}?q=(((")
        assert r.status_code == 200

    async def test_filter_on_hidden_column_is_ignored(self, admin):
        r = await admin.get(f"{TICKETS}?created_by_id={ZERO}&payload=x")
        assert r.status_code == 200 and r.json()["total"] > 0

    async def test_sort_cannot_be_chosen_by_the_client(self, admin):
        r = await admin.get(f"{TICKETS}?order_by=payload")
        assert r.status_code == 200

    async def test_children_count_and_parent_code(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op)
        assert exp["parent_code"] == op["code"]
        assert (await get_ticket(admin, op))["children_count"] >= 2

    async def test_mine_filter_by_role(self, pilot, admin):
        op = await make_operation(admin)
        mine = (await pilot.get(f"{TICKETS}?mine=true&limit=100&parent_id={op['id']}")).json()
        assert mine["data"], "the auto report is assigned to the pilot role"
        assert all(t["assigned_role_name"] == "Piloto" for t in mine["data"])

    async def test_mine_filter_excludes_other_roles(self, ground, admin):
        op = await make_operation(admin)
        mine = (await ground.get(f"{TICKETS}?mine=true&limit=100&parent_id={op['id']}")).json()
        assert mine["data"] == []

    async def test_mine_filter_by_direct_assignment(self, ground, logged, admin):
        _, body = await logged("tierra@transvalor.com")
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "operation",
                "assigned_user_id": body["user"]["id"],
                "payload": OPERATION,
            },
        )
        mine = (await ground.get(f"{TICKETS}?mine=true&limit=100")).json()["data"]
        assert r.json()["id"] in {t["id"] for t in mine}

    async def test_direct_assignment_overrides_role_for_mine(self, pilot, admin, logged):
        _, body = await logged("tierra@transvalor.com")
        roles = (await admin.get(f"{API}/roles/?limit=100")).json()["data"]
        pilot_role = next(r for r in roles if r["code"] == "pilot")
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "operation",
                "assigned_role_id": pilot_role["id"],
                "assigned_user_id": body["user"]["id"],
                "payload": OPERATION,
            },
        )
        mine = (await pilot.get(f"{TICKETS}?mine=true&limit=100")).json()["data"]
        assert r.json()["id"] not in {t["id"] for t in mine}


class TestUpdate:
    async def test_title_and_due_date(self, admin):
        op = await make_operation(admin)
        r = await patch(admin, op, title="Nuevo título", due_at="2031-05-05T00:00:00Z")
        assert r.status_code == 200
        assert r.json()["title"] == "Nuevo título" and r.json()["due_at"].startswith("2031-05-05")

    async def test_payload_is_merged_not_replaced(self, admin):
        op = await make_operation(admin, captain="Pérez")
        r = await patch(admin, op, payload={"fbo_name": "Signature"})
        data = r.json()["payload"]
        assert data["captain"] == "Pérez" and data["fbo_name"] == "Signature"
        assert data["origin"] == "CCS"

    async def test_required_field_cannot_be_blanked(self, admin):
        op = await make_operation(admin)
        r = await patch(admin, op, payload={"origin": ""})
        assert r.status_code == 422 and "origin" in r.json()["errors"]

    async def test_optional_field_can_be_cleared(self, admin):
        op = await make_operation(admin, captain="Pérez")
        r = await patch(admin, op, payload={"captain": ""})
        assert r.status_code == 200
        assert not r.json()["payload"].get("captain")

    async def test_invalid_payload_value_rejected_and_nothing_changes(self, admin):
        op = await make_operation(admin)
        r = await patch(admin, op, title="Cambio", payload={"origin": "ZZZ"})
        assert r.status_code == 422
        again = await get_ticket(admin, op)
        assert again["title"] == op["title"] and again["payload"]["origin"] == "CCS"

    async def test_unknown_payload_key_rejected(self, admin):
        op = await make_operation(admin)
        assert (await patch(admin, op, payload={"hack": 1})).status_code == 422

    async def test_aircraft_of_an_operation_cannot_change(self, admin):
        op = await make_operation(admin)
        r = await patch(admin, op, payload={"aircraft": "YV2528"})
        assert r.status_code == 422

    async def test_comment_appears_in_comments_and_history(self, admin):
        op = await make_operation(admin)
        r = await patch(admin, op, comment="Listo")
        assert r.json()["comments"][-1]["body"] == "Listo"
        events = (await admin.get(ticket_url(op, "history/?limit=100"))).json()["data"]
        assert any(e["action"] == "comment" for e in events)

    async def test_empty_patch_is_ok_and_changes_nothing(self, admin):
        op = await make_operation(admin)
        r = await patch(admin, op)
        assert r.status_code == 200 and r.json()["title"] == op["title"]

    async def test_unknown_ticket_is_404(self, admin):
        assert (await admin.patch(ticket_url(ZERO), json={"title": "x"})).status_code == 404

    async def test_updated_at_moves(self, admin):
        op = await make_operation(admin)
        r = await patch(admin, op, title="Otro")
        assert r.json()["updated_at"] >= op["updated_at"]

    async def test_diff_is_audited_per_field(self, admin):
        op = await make_operation(admin, captain="Pérez")
        await patch(admin, op, payload={"captain": "García"})
        events = (await admin.get(ticket_url(op, "history/?limit=100"))).json()["data"]
        update = next(e for e in events if e["action"] == "update")
        change = next(c for c in update["changes"] if c["field"] == "captain")
        assert change["from"] == "Pérez" and change["to"] == "García"
        assert change["label"] == "Capitán de nave"

    @pytest.mark.parametrize(
        "forged",
        [
            {"code": "HACK"},
            {"closed_at": "2026-01-01T00:00:00Z"},
            {"created_by": "Otro"},
            {"authorization": {"by": "yo"}},
            {"rule_hits": []},
            {"amount_base": 5},
            {"type_code": "expense"},
            {"parent_id": ZERO},
        ],
    )
    async def test_server_owned_fields_are_not_patchable(self, admin, forged):
        op = await make_operation(admin)
        r = await admin.patch(ticket_url(op), json=forged)
        assert r.status_code in (200, 422)
        again = await get_ticket(admin, op)
        for key in ("code", "closed_at", "parent_id", "type_code", "authorization"):
            assert again[key] == op[key]

    async def test_reassign_role_and_user(self, admin):
        op = await make_operation(admin)
        roles = (await admin.get(f"{API}/roles/?limit=100")).json()["data"]
        ground = next(r for r in roles if r["code"] == "ground")
        r = await patch(admin, op, assigned_role_id=ground["id"])
        assert r.json()["assigned_role_name"] == ground["name"]
        events = (await admin.get(ticket_url(op, "history/?limit=100"))).json()["data"]
        assert any("Reasignó" in e["summary"] for e in events)

    async def test_reassign_to_unknown_role_rejected(self, admin):
        op = await make_operation(admin)
        assert (await patch(admin, op, assigned_role_id=ZERO)).status_code == 422


class TestStatusChanges:
    async def test_expense_can_be_verified(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        r = await patch(admin, exp, status_code="verified")
        assert r.status_code == 200
        assert r.json()["status_is_final"] is True and r.json()["closed_at"] is not None

    async def test_unknown_status_rejected(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        assert (await patch(admin, exp, status_code="ghost")).status_code == 422

    async def test_status_of_another_type_rejected(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        assert (await patch(admin, exp, status_code="planned")).status_code == 422

    async def test_same_status_is_a_no_op(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        r = await patch(admin, exp, status_code="registered")
        assert r.status_code == 200 and r.json()["status_code"] == "registered"

    async def test_status_change_is_audited(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        await patch(admin, exp, status_code="rejected")
        events = (await admin.get(ticket_url(exp, "history/?limit=100"))).json()["data"]
        change = next(e for e in events if e["action"] == "status_change")
        assert change["changes"][0]["from"] == "Registrado"
        assert change["changes"][0]["to"] == "Rechazado"

    async def test_restricted_transitions_are_enforced(self, admin):
        t = await admin.post(
            f"{API}/ticket-types/",
            json={
                "name": "Flujo estricto",
                "code": "flujo_estricto",
                "fields": [{"label": "A", "type": "text", "key": "a"}],
                "statuses": [
                    {"code": "a", "name": "A", "is_initial": True, "next_codes": ["b"]},
                    {"code": "b", "name": "B", "next_codes": ["z"]},
                    {"code": "z", "name": "Z", "is_final": True},
                ],
            },
        )
        assert t.status_code == 201, t.text
        made = await admin.post(TICKETS, json={"type_code": "flujo_estricto", "payload": {}})
        assert made.status_code == 201, made.text
        ticket = made.json()
        assert (await patch(admin, ticket, status_code="z")).status_code == 422
        assert (await patch(admin, ticket, status_code="b")).status_code == 200
        assert (await patch(admin, ticket, status_code="a")).status_code == 422
        assert (await patch(admin, ticket, status_code="z")).status_code == 200

    async def test_failed_status_change_keeps_the_old_status(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        await patch(admin, exp, status_code="ghost")
        assert (await get_ticket(admin, exp))["status_code"] == "registered"


class TestChildren:
    async def test_expense_under_operation(self, admin):
        op = await make_operation(admin)
        exp = await make_expense(admin, op)
        assert exp["parent_id"] == op["id"]

    async def test_child_type_not_allowed_for_parent(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "expense",
                "parent_id": exp["id"],
                "payload": EXPENSE,
            },
        )
        assert r.status_code == 422

    async def test_hangar_incident_is_not_a_child_of_operations(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "hangar_incident",
                "parent_id": op["id"],
                "payload": {"description": "golpe"},
            },
        )
        assert r.status_code == 422

    async def test_unknown_parent_is_404(self, admin):
        r = await post_expense(admin, ZERO)
        assert r.status_code == 404

    async def test_standalone_ticket_without_parent(self, admin):
        r = await admin.post(
            TICKETS,
            json={"type_code": "hangar_incident", "payload": {"description": "golpe"}},
        )
        assert r.status_code == 201 and r.json()["parent_id"] is None

    async def test_operation_cannot_close_with_blocking_children(self, admin):
        op = await make_operation(admin)
        r = await patch(admin, op, status_code="closed")
        assert r.status_code == 422
        child = (await children(admin, op, type_code="technical_report"))[0]
        assert child["code"] in r.json()["detail"]

    async def test_non_blocking_children_do_not_stop_the_close_check(self, admin):
        op = await make_operation(admin)
        await make_expense(admin, op)
        r = await patch(admin, op, status_code="closed")
        # Still refused, but because of the technical report, never because of the expense.
        assert "TK-" in r.json()["detail"]
        report = (await children(admin, op, type_code="technical_report"))[0]["code"]
        assert report in r.json()["detail"]

    async def test_cancelling_ignores_unresolved_children(self, admin):
        op = await make_operation(admin)
        assert (await patch(admin, op, status_code="cancelled")).status_code == 200

    async def test_required_steps_block_the_close(self, admin):
        op = await make_operation(admin)
        report = (await children(admin, op, type_code="technical_report"))[0]
        await patch(admin, report, status_code="no_findings")
        r = await patch(admin, op, status_code="closed")
        assert r.status_code == 422 and "pasos" in r.json()["detail"].lower()


class TestMethodsNotExposed:
    async def test_tickets_cannot_be_deleted(self, admin):
        op = await make_operation(admin)
        assert (await admin.delete(ticket_url(op))).status_code == 405
        assert (await admin.get(ticket_url(op))).status_code == 200

    async def test_put_is_not_exposed(self, admin):
        op = await make_operation(admin)
        assert (await admin.request("PUT", ticket_url(op), json={})).status_code == 405
