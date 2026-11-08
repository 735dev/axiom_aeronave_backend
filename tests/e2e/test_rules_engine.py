import pytest

from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    get_ticket,
    history,
    make_expense,
    make_operation,
    patch,
    post_expense,
    rule_named,
    ticket_url,
)

RULES = f"{API}/rules/"
ZERO = "00000000-0000-0000-0000-000000000000"


def hit_names(ticket):
    return {h["rule_name"] for h in ticket["rule_hits"]}


async def big(api, amount, op=None, **payload):
    parent = op or await make_operation(api)
    return await make_expense(api, parent, amount=amount, **payload)


def rule_body(**over):
    body = {
        "name": f"Regla {over.pop('tag', 'x')} larga",
        "type_code": "expense",
        "field": "amount_base",
        "operator": "gt",
        "value": 99999,
        "action": "warn",
        "message": "Mensaje de la regla",
    }
    body.update(over)
    return body


class TestFiring:
    async def test_over_500_requires_authorization(self, admin):
        t = await big(admin, 600)
        assert "Gasto mayor a 500 USD requiere autorización" in hit_names(t)
        hit = next(h for h in t["rule_hits"] if h["rule_name"].startswith("Gasto mayor"))
        assert hit["action"] == "require_authorization"

    async def test_100_does_not_fire_it(self, admin):
        assert "Gasto mayor a 500 USD requiere autorización" not in hit_names(await big(admin, 100))

    async def test_the_boundary_is_strict(self, admin):
        assert not any(
            h["rule_name"].startswith("Gasto mayor") for h in (await big(admin, 500))["rule_hits"]
        )
        assert any(
            h["rule_name"].startswith("Gasto mayor")
            for h in (await big(admin, 500.01))["rule_hits"]
        )

    async def test_the_rule_uses_the_base_amount_not_the_original(self, admin):
        op = await make_operation(admin, exchange_rate=40)
        t = await make_expense(admin, op, currency="VES", amount=40000)
        assert any(h["rule_name"].startswith("Gasto mayor") for h in t["rule_hits"])
        small = await make_expense(admin, op, currency="VES", amount=400)
        assert not any(h["rule_name"].startswith("Gasto mayor") for h in small["rule_hits"])

    async def test_missing_support_fires_without_attachments(self, admin):
        t = await big(admin, 10)
        hit = next(h for h in t["rule_hits"] if h["action"] == "require_support")
        assert hit["message"] == "El gasto no tiene comprobante adjunto."

    async def test_concept_rule_warns_on_navaid(self, admin):
        t = await big(admin, 10, concept="navaid")
        hit = next(h for h in t["rule_hits"] if h["rule_name"].startswith("Radioayuda"))
        assert hit["action"] == "warn"

    async def test_concept_rule_ignores_other_concepts(self, admin):
        t = await big(admin, 10, concept="fuel")
        assert not any(h["rule_name"].startswith("Radioayuda") for h in t["rule_hits"])

    async def test_rules_only_apply_to_their_type(self, admin):
        op = await make_operation(admin)
        assert op["rule_hits"] == []

    async def test_firing_is_audited_with_the_reason(self, admin):
        t = await big(admin, 700)
        summaries = [e["summary"] for e in await history(admin, t)]
        assert any('Se disparó la regla "Gasto mayor' in s and t["code"] in s for s in summaries)

    async def test_recalculating_does_not_repeat_the_entry(self, admin):
        t = await big(admin, 700)
        await patch(admin, t, payload={"notes": "a"})
        await patch(admin, t, payload={"notes": "b"})
        entries = [
            e for e in await history(admin, t) if 'Se disparó la regla "Gasto mayor' in e["summary"]
        ]
        assert len(entries) == 1

    async def test_recalculating_keeps_the_original_timestamp(self, admin):
        t = await big(admin, 700)
        at = next(h["at"] for h in t["rule_hits"] if h["rule_name"].startswith("Gasto mayor"))
        await patch(admin, t, payload={"notes": "a"})
        again = await get_ticket(admin, t)
        assert (
            next(h["at"] for h in again["rule_hits"] if h["rule_name"].startswith("Gasto mayor"))
            == at
        )

    async def test_a_hit_that_no_longer_applies_is_dropped(self, admin):
        t = await big(admin, 700)
        edited = await patch(admin, t, payload={"amount": 100})
        assert not any(h["rule_name"].startswith("Gasto mayor") for h in edited.json()["rule_hits"])

    async def test_editing_into_range_fires_on_update(self, admin):
        t = await big(admin, 100)
        edited = await patch(admin, t, payload={"amount": 900})
        assert any(h["rule_name"].startswith("Gasto mayor") for h in edited.json()["rule_hits"])
        entries = [
            e for e in await history(admin, t) if 'Se disparó la regla "Gasto mayor' in e["summary"]
        ]
        assert len(entries) == 1

    async def test_each_rule_is_recorded_separately(self, admin):
        t = await big(admin, 900, concept="navaid")
        names = hit_names(t)
        assert len(names) >= 3

    async def test_hit_carries_the_rule_id(self, admin):
        t = await big(admin, 900)
        rule = await rule_named(admin, "Gasto mayor")
        assert rule["id"] in {h["rule_id"] for h in t["rule_hits"]}


class TestRouteRule:
    async def test_over_the_route_limit_fires(self, admin):
        t = await big(admin, 1300)
        assert any(h["rule_name"].startswith("Gasto fuera de rango") for h in t["rule_hits"])

    async def test_under_the_route_limit_does_not(self, admin):
        t = await big(admin, 1100)
        assert not any(h["rule_name"].startswith("Gasto fuera de rango") for h in t["rule_hits"])

    async def test_each_route_has_its_own_limit(self, admin):
        op = await make_operation(admin, destination="SVSA")
        assert any(
            h["rule_name"].startswith("Gasto fuera de rango")
            for h in (await make_expense(admin, op, amount=1000))["rule_hits"]
        )
        assert not any(
            h["rule_name"].startswith("Gasto fuera de rango")
            for h in (await make_expense(admin, op, amount=800))["rule_hits"]
        )

    async def test_a_route_without_a_limit_invents_none(self, admin):
        op = await make_operation(admin, origin="MDPC", destination="SVBC")
        t = await make_expense(admin, op, amount=99999)
        assert not any(h["rule_name"].startswith("Gasto fuera de rango") for h in t["rule_hits"])

    async def test_the_limit_is_read_from_the_operation_route(self, admin):
        op = await make_operation(admin)
        route_rule = await rule_named(admin, "fuera de rango")
        assert route_rule["route_limits"]["CCS-PZO"] == 1200

    async def test_changing_the_route_limits(self, admin):
        rule = await rule_named(admin, "fuera de rango")
        original = rule["route_limits"]
        try:
            r = await admin.patch(f"{RULES}{rule['id']}/", json={"route_limits": {"XXX-YYY": 1}})
            assert r.status_code == 200 and r.json()["route_limits"] == {"XXX-YYY": 1}
            t = await big(admin, 1300)
            assert not any(
                h["rule_name"].startswith("Gasto fuera de rango") for h in t["rule_hits"]
            )
        finally:
            await admin.patch(f"{RULES}{rule['id']}/", json={"route_limits": original})

    async def test_route_rule_creation(self, admin):
        r = await admin.post(
            RULES,
            json=rule_body(
                tag="ruta",
                field="amount_vs_route",
                value=None,
                route_limits={"CCS-PZO": 10},
            ),
        )
        assert r.status_code == 201 and r.json()["route_limits"] == {"CCS-PZO": 10}
        await admin.delete(f"{RULES}{r.json()['id']}/")

    async def test_route_rule_needs_limits(self, admin):
        r = await admin.post(RULES, json=rule_body(field="amount_vs_route", value=None))
        assert r.status_code == 422

    @pytest.mark.parametrize(
        "limits", [{"ccs pzo": 10}, {"CCS": 10}, {"CCS-PZO": 0}, {"CCS-PZO": -1}]
    )
    async def test_invalid_route_limits_rejected(self, admin, limits):
        r = await admin.post(
            RULES, json=rule_body(field="amount_vs_route", value=None, route_limits=limits)
        )
        assert r.status_code == 422


class TestAuthorizationGate:
    async def test_cannot_verify_a_flagged_expense_without_authorization(self, admin):
        t = await big(admin, 800)
        r = await patch(admin, t, status_code="verified")
        assert r.status_code == 422 and "autorizaci" in r.json()["detail"]

    async def test_rejecting_needs_no_authorization(self, admin):
        t = await big(admin, 800)
        assert (await patch(admin, t, status_code="rejected")).status_code == 200

    async def test_unflagged_expenses_verify_freely(self, admin):
        t = await big(admin, 50)
        assert (await patch(admin, t, status_code="verified")).status_code == 200

    async def test_without_the_permission_authorizing_is_403(self, pilot, admin):
        t = await big(admin, 800)
        assert (await pilot.post(ticket_url(t, "authorize/"), json={})).status_code == 403
        assert (await get_ticket(admin, t))["authorization"] is None

    async def test_management_authorizes_and_it_is_sealed(self, director, admin):
        t = await big(admin, 800)
        r = await director.post(ticket_url(t, "authorize/"), json={"note": "ok"})
        assert r.status_code == 200, r.text
        auth = r.json()["authorization"]
        assert auth["amount_base"] == 800 and auth["note"] == "ok"
        assert auth["by"] and auth["role"] and auth["at"]

    async def test_authorized_expense_can_be_verified(self, director, admin):
        t = await big(admin, 800)
        await director.post(ticket_url(t, "authorize/"), json={})
        assert (await patch(admin, t, status_code="verified")).status_code == 200

    async def test_authorization_is_recorded_as_a_decision(self, director, admin):
        t = await big(admin, 800)
        await director.post(ticket_url(t, "authorize/"), json={})
        events = await history(admin, t)
        assert any(e["action"] == "decision" and "autorizó" in e["summary"] for e in events)

    async def test_changing_the_amount_voids_the_authorization(self, director, admin):
        t = await big(admin, 800)
        await director.post(ticket_url(t, "authorize/"), json={})
        await patch(admin, t, payload={"amount": 900})
        r = await patch(admin, t, status_code="verified")
        assert r.status_code == 422

    async def test_authorizing_again_after_the_change_works(self, director, admin):
        t = await big(admin, 800)
        await director.post(ticket_url(t, "authorize/"), json={})
        await patch(admin, t, payload={"amount": 900})
        await director.post(ticket_url(t, "authorize/"), json={})
        assert (await patch(admin, t, status_code="verified")).status_code == 200

    async def test_lowering_below_the_threshold_removes_the_need(self, director, admin):
        t = await big(admin, 800)
        await patch(admin, t, payload={"amount": 100})
        assert (await patch(admin, t, status_code="verified")).status_code == 200

    async def test_nothing_to_authorize_is_422(self, director, admin):
        t = await big(admin, 50)
        assert (await director.post(ticket_url(t, "authorize/"), json={})).status_code == 422

    async def test_authorize_unknown_ticket_is_404(self, director):
        assert (await director.post(ticket_url(ZERO, "authorize/"), json={})).status_code == 404

    async def test_authorize_note_has_a_limit(self, director, admin):
        t = await big(admin, 800)
        r = await director.post(ticket_url(t, "authorize/"), json={"note": "x" * 5000})
        assert r.status_code == 422

    async def test_client_cannot_write_the_authorization_through_patch(self, admin):
        t = await big(admin, 800)
        await admin.patch(
            ticket_url(t),
            json={"authorization": {"by": "yo", "amount_base": 800}, "status_code": "verified"},
        )
        assert (await get_ticket(admin, t))["authorization"] is None
        assert (await get_ticket(admin, t))["status_code"] != "verified"

    async def test_unflagged_authorization_cannot_be_forged_via_payload(self, admin):
        r = await post_expense(admin, await make_operation(admin), authorization={"by": "x"})
        assert r.status_code == 422

    async def test_the_operation_cannot_close_with_a_pending_flagged_expense(self, admin):
        op = await make_operation(admin)
        await make_expense(admin, op, amount=800)
        assert (await patch(admin, op, status_code="closed")).status_code == 422

    async def test_three_flagged_expenses_all_need_their_own_authorization(self, director, admin):
        op = await make_operation(admin)
        a = await make_expense(admin, op, amount=800)
        b = await make_expense(admin, op, amount=900)
        await director.post(ticket_url(a, "authorize/"), json={})
        assert (await patch(admin, a, status_code="verified")).status_code == 200
        assert (await patch(admin, b, status_code="verified")).status_code == 422

    async def test_authorization_does_not_enable_editing(self, director, admin):
        t = await big(admin, 800)
        assert (await director.patch(ticket_url(t), json={"title": "x"})).status_code == 403


class TestRuleAdministration:
    async def test_list_has_the_seeded_rules(self, admin):
        names = {r["name"] for r in (await admin.get(RULES)).json()}
        assert any(n.startswith("Gasto mayor") for n in names)
        assert len(names) >= 4

    async def test_list_filters_by_type(self, admin):
        assert (await admin.get(f"{RULES}?type_code=operation")).json() == []
        rows = (await admin.get(f"{RULES}?type_code=expense")).json()
        assert rows and {r["type_code"] for r in rows} == {"expense"}

    async def test_create_and_read(self, admin):
        r = await admin.post(RULES, json=rule_body(tag="crear"))
        assert r.status_code == 201
        body = r.json()
        assert body["type_code"] == "expense" and body["value"] == 99999
        assert (await admin.get(f"{RULES}{body['id']}/")).json()["name"] == body["name"]

    async def test_edit_the_message(self, admin):
        rule = await admin.post(RULES, json=rule_body(tag="editar"))
        rid = rule.json()["id"]
        r = await admin.patch(f"{RULES}{rid}/", json={"message": "nuevo mensaje"})
        assert r.json()["message"] == "nuevo mensaje"
        listed = (await admin.get(RULES)).json()
        assert next(x for x in listed if x["id"] == rid)["message"] == "nuevo mensaje"

    async def test_an_inactive_rule_does_not_fire(self, admin):
        rule = await rule_named(admin, "Gasto mayor")
        try:
            await admin.patch(f"{RULES}{rule['id']}/", json={"is_active": False})
            t = await big(admin, 900)
            assert not any(h["rule_name"].startswith("Gasto mayor") for h in t["rule_hits"])
        finally:
            await admin.patch(f"{RULES}{rule['id']}/", json={"is_active": True})

    async def test_the_warning_text_is_frozen_when_the_rule_is_edited(self, admin):
        rule = await rule_named(admin, "Radioayuda")
        t = await big(admin, 10, concept="navaid")
        before = next(h["message"] for h in t["rule_hits"] if h["rule_id"] == rule["id"])
        try:
            await admin.patch(f"{RULES}{rule['id']}/", json={"message": "otro texto distinto"})
            await patch(admin, t, payload={"notes": "x"})
            again = await get_ticket(admin, t)
            assert (
                next(h["message"] for h in again["rule_hits"] if h["rule_id"] == rule["id"])
                == before
            )
        finally:
            await admin.patch(f"{RULES}{rule['id']}/", json={"message": rule["message"]})

    async def test_delete(self, admin):
        rid = (await admin.post(RULES, json=rule_body(tag="borrar"))).json()["id"]
        assert (await admin.delete(f"{RULES}{rid}/")).status_code == 204
        assert (await admin.get(f"{RULES}{rid}/")).status_code == 404

    async def test_deleted_rule_keeps_its_past_hits(self, admin):
        rid = (
            await admin.post(RULES, json=rule_body(tag="pasado", value=1, message="Pasado"))
        ).json()["id"]
        try:
            t = await big(admin, 5)
            assert rid in {h["rule_id"] for h in t["rule_hits"]}
            await admin.delete(f"{RULES}{rid}/")
            assert rid in {h["rule_id"] for h in (await get_ticket(admin, t))["rule_hits"]}
        finally:
            await admin.delete(f"{RULES}{rid}/")

    async def test_unknown_type_rejected(self, admin):
        assert (await admin.post(RULES, json=rule_body(type_code="ghost"))).status_code == 422

    @pytest.mark.parametrize(
        ("field", "operator"),
        [("attachments", "gt"), ("concept", "gt"), ("amount_base", "missing"), ("concept", "lt")],
    )
    async def test_operator_must_fit_the_field(self, admin, field, operator):
        r = await admin.post(RULES, json=rule_body(field=field, operator=operator, value="a"))
        assert r.status_code == 422

    async def test_value_is_required_for_comparisons(self, admin):
        assert (await admin.post(RULES, json=rule_body(value=None))).status_code == 422

    async def test_amount_value_must_be_numeric(self, admin):
        assert (await admin.post(RULES, json=rule_body(value="mucho"))).status_code == 422

    async def test_unknown_field_and_action_rejected(self, admin):
        assert (await admin.post(RULES, json=rule_body(field="color"))).status_code == 422
        assert (await admin.post(RULES, json=rule_body(action="explode"))).status_code == 422

    async def test_short_name_and_message_rejected(self, admin):
        assert (await admin.post(RULES, json=rule_body(name="ab"))).status_code == 422
        assert (await admin.post(RULES, json=rule_body(message="ab"))).status_code == 422

    async def test_changing_the_field_resets_what_depended_on_it(self, admin):
        rid = (await admin.post(RULES, json=rule_body(tag="cambia"))).json()["id"]
        r = await admin.patch(
            f"{RULES}{rid}/", json={"field": "concept", "operator": "eq", "value": "ice"}
        )
        assert r.status_code == 200 and r.json()["value"] == "ice"

    async def test_patch_unknown_rule_is_404(self, admin):
        assert (
            await admin.patch(f"{RULES}{ZERO}/", json={"message": "hola mundo"})
        ).status_code == 404

    async def test_null_name_rejected(self, admin):
        rid = (await admin.post(RULES, json=rule_body(tag="nulo"))).json()["id"]
        assert (await admin.patch(f"{RULES}{rid}/", json={"name": None})).status_code == 422

    @pytest.mark.parametrize("method", ["get", "post"])
    async def test_pilot_cannot_use_rules(self, pilot, method):
        r = await pilot.request(method.upper(), RULES, json=rule_body())
        assert r.status_code == 403

    async def test_pilot_cannot_edit_or_delete_rules(self, pilot, admin):
        rid = (await admin.post(RULES, json=rule_body(tag="ajena"))).json()["id"]
        assert (
            await pilot.patch(f"{RULES}{rid}/", json={"message": "hackeado!"})
        ).status_code == 403
        assert (await pilot.delete(f"{RULES}{rid}/")).status_code == 403
        assert (await admin.get(f"{RULES}{rid}/")).json()["message"] == "Mensaje de la regla"

    async def test_rule_changes_are_audited(self, admin):
        rid = (await admin.post(RULES, json=rule_body(tag="auditada"))).json()["id"]
        await admin.patch(f"{RULES}{rid}/", json={"message": "otro mensaje distinto"})
        events = (await admin.get(f"{API}/audit/?entity=ticket_type&limit=100")).json()["data"]
        mine = [e for e in events if e["entity_id"] == rid]
        assert {"create", "update"} <= {e["action"] for e in mine}
