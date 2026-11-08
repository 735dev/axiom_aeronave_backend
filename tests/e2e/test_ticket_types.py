import pytest

from tests.e2e.conftest import API, SEEDED_TYPE_CODES
from tests.e2e.tickets_support import (
    field,
    make_expense,
    make_operation,
    new_key,
    status,
    type_body,
    type_by_code,
)

TYPES = f"{API}/ticket-types/"


async def create_type(api, **over):
    r = await api.post(TYPES, json=type_body(**over))
    assert r.status_code == 201, r.text
    return r.json()


class TestListing:
    async def test_lists_the_five_seeded_types(self, admin):
        codes = {t["code"] for t in (await admin.get(TYPES)).json()}
        assert set(SEEDED_TYPE_CODES) <= codes

    async def test_shape(self, admin):
        t = await type_by_code(admin, "expense")
        assert {"id", "code", "name", "fields", "statuses", "steps", "is_root"} <= t.keys()
        assert {"key", "label", "type", "required", "role", "sort_order"} <= t["fields"][0].keys()
        assert {"code", "is_initial", "is_final", "counts_as_committed"} <= t["statuses"][0].keys()

    async def test_is_root_filter(self, admin):
        roots = (await admin.get(f"{TYPES}?is_root=true")).json()
        assert roots and all(t["is_root"] for t in roots)
        assert "operation" in {t["code"] for t in roots}
        others = (await admin.get(f"{TYPES}?is_root=false")).json()
        assert others and not any(t["is_root"] for t in others)

    async def test_is_active_filter(self, admin):
        created = await create_type(admin)
        await admin.patch(f"{TYPES}{created['id']}/", json={"is_active": False})
        inactive = {t["code"] for t in (await admin.get(f"{TYPES}?is_active=false")).json()}
        active = {t["code"] for t in (await admin.get(f"{TYPES}?is_active=true")).json()}
        assert created["code"] in inactive and created["code"] not in active

    async def test_unknown_filter_is_ignored_not_resolved(self, admin):
        r = await admin.get(f"{TYPES}?id=whatever&name=x")
        assert r.status_code == 200

    async def test_operation_roles_are_semantic(self, admin):
        t = await type_by_code(admin, "expense")
        roles = {f["role"]: f["key"] for f in t["fields"] if f["role"]}
        assert roles["amount"] == "amount" and roles["currency"] == "currency"
        assert roles["item"] == "concept"

    async def test_expense_money_status_flags(self, admin):
        t = await type_by_code(admin, "expense")
        by_code = {s["code"]: s for s in t["statuses"]}
        assert by_code["verified"]["is_final"] and by_code["verified"]["counts_as_committed"]
        assert by_code["rejected"]["is_final"] and not by_code["rejected"]["counts_as_committed"]
        assert by_code["registered"]["is_initial"] and not by_code["registered"]["is_final"]

    async def test_technical_report_has_no_findings_status(self, admin):
        t = await type_by_code(admin, "technical_report")
        no_findings = next(s for s in t["statuses"] if s["code"] == "no_findings")
        assert no_findings["is_final"] and not no_findings["counts_as_committed"]

    async def test_operation_relations_and_children(self, admin):
        t = await type_by_code(admin, "operation")
        assert t["is_root"] and set(t["child_type_codes"]) >= {"expense", "technical_report"}
        assert "aircraft" in t["relation_kinds"]

    async def test_blocks_parent_close_flags(self, admin):
        assert (await type_by_code(admin, "technical_report"))["blocks_parent_close"] is True
        assert (await type_by_code(admin, "expense"))["blocks_parent_close"] is False

    async def test_retrieve(self, admin):
        t = await type_by_code(admin, "expense")
        r = await admin.get(f"{TYPES}{t['id']}/")
        assert r.status_code == 200 and r.json()["code"] == "expense"

    async def test_retrieve_unknown_is_404(self, admin):
        r = await admin.get(f"{TYPES}00000000-0000-0000-0000-000000000000/")
        assert r.status_code == 404

    async def test_pilot_reads_forms(self, pilot):
        assert (await pilot.get(TYPES)).status_code == 200

    async def test_tickets_count_reflects_usage(self, admin):
        before = (await type_by_code(admin, "operation"))["tickets_count"]
        await make_operation(admin)
        assert (await type_by_code(admin, "operation"))["tickets_count"] >= before + 1


class TestScopedDefinitions:
    async def test_per_type_viewer_sees_only_that_form(self, new_account):
        api, _, _ = await new_account(["tickets.view.expense"])
        codes = {t["code"] for t in (await api.get(TYPES)).json()}
        assert codes == {"expense"}

    async def test_per_type_viewer_cannot_open_other_definition(self, new_account, admin):
        api, _, _ = await new_account(["tickets.view.expense"])
        other = await type_by_code(admin, "operation")
        assert (await api.get(f"{TYPES}{other['id']}/")).status_code == 404

    async def test_creator_of_one_type_sees_its_form(self, new_account):
        api, _, _ = await new_account(["tickets.create.hangar_incident"])
        assert {t["code"] for t in (await api.get(TYPES)).json()} == {"hangar_incident"}

    async def test_user_without_ticket_permissions_gets_403(self, new_account):
        api, _, _ = await new_account(["catalogs.view"])
        assert (await api.get(TYPES)).status_code == 403


class TestCreate:
    async def test_creates_a_type_with_defaults(self, admin):
        t = await create_type(admin)
        assert t["is_system"] is False and t["is_active"] is True
        assert [s["code"] for s in t["statuses"]] == ["open", "done"]
        assert t["statuses"][0]["counts_as_committed"] is True

    async def test_code_is_derived_from_the_name(self, admin):
        body = type_body()
        body.pop("code")
        body["name"] = f"Inspección {new_key()[-6:]}"
        r = await admin.post(TYPES, json=body)
        assert r.status_code == 201
        assert r.json()["code"].startswith("inspeccion")

    async def test_derived_permissions_are_registered(self, admin):
        t = await create_type(admin)
        codes = {p["code"] for p in (await admin.get(f"{API}/permissions/")).json()}
        for base in ("view", "create", "edit"):
            assert f"tickets.{base}.{t['code']}" in codes

    async def test_superuser_gets_the_derived_permissions(self, admin):
        t = await create_type(admin)
        me = (await admin.get(f"{API}/auth/me/")).json()
        assert f"tickets.edit.{t['code']}" in me["permissions"]

    async def test_derived_permission_can_be_granted_to_a_role(self, admin, new_account):
        t = await create_type(admin)
        api, _, _ = await new_account([f"tickets.view.{t['code']}", f"tickets.create.{t['code']}"])
        r = await api.post(
            f"{API}/tickets/",
            json={"type_code": t["code"], "title": "x", "payload": {"detail": "hola"}},
        )
        assert r.status_code == 201, r.text

    async def test_audit_event_is_written(self, admin):
        t = await create_type(admin)
        events = (await admin.get(f"{API}/audit/?entity=ticket_type&entity_id={t['id']}")).json()
        assert any(e["action"] == "create" for e in events["data"])

    async def test_duplicate_code_rejected(self, admin):
        t = await create_type(admin)
        r = await admin.post(TYPES, json=type_body(code=t["code"]))
        assert r.status_code == 422

    async def test_seeded_code_cannot_be_reused(self, admin):
        r = await admin.post(TYPES, json=type_body(code="expense"))
        assert r.status_code == 422

    @pytest.mark.parametrize("code", ["Bad", "1abc", "has-dash", "a b", "x" * 61])
    async def test_invalid_code_rejected(self, admin, code):
        assert (await admin.post(TYPES, json=type_body(code=code))).status_code == 422

    async def test_short_name_rejected(self, admin):
        assert (await admin.post(TYPES, json=type_body(name="ab"))).status_code == 422

    async def test_no_fields_rejected(self, admin):
        assert (await admin.post(TYPES, json=type_body(fields=[]))).status_code == 422

    async def test_no_statuses_rejected(self, admin):
        assert (await admin.post(TYPES, json=type_body(statuses=[]))).status_code == 422

    async def test_needs_a_final_status(self, admin):
        r = await admin.post(
            TYPES, json=type_body(statuses=[status("open", is_initial=True), status("wip")])
        )
        assert r.status_code == 422

    async def test_two_initial_statuses_rejected(self, admin):
        statuses = [
            status("a", is_initial=True),
            status("b", is_initial=True),
            status("z", is_final=True),
        ]
        assert (await admin.post(TYPES, json=type_body(statuses=statuses))).status_code == 422

    async def test_first_status_is_initial_by_default(self, admin):
        t = await create_type(admin, statuses=[status("a"), status("z", is_final=True)])
        assert t["statuses"][0]["is_initial"] is True

    async def test_initial_status_cannot_be_final(self, admin):
        statuses = [status("a", is_initial=True, is_final=True)]
        assert (await admin.post(TYPES, json=type_body(statuses=statuses))).status_code == 422

    async def test_duplicate_status_code_rejected(self, admin):
        statuses = [status("a", is_initial=True), status("a"), status("z", is_final=True)]
        assert (await admin.post(TYPES, json=type_body(statuses=statuses))).status_code == 422

    async def test_transition_to_unknown_status_rejected(self, admin):
        statuses = [
            status("a", is_initial=True, next_codes=["ghost"]),
            status("z", is_final=True),
        ]
        assert (await admin.post(TYPES, json=type_body(statuses=statuses))).status_code == 422

    async def test_final_status_cannot_have_transitions(self, admin):
        statuses = [status("a", is_initial=True), status("z", is_final=True, next_codes=["a"])]
        assert (await admin.post(TYPES, json=type_body(statuses=statuses))).status_code == 422

    async def test_status_cannot_transition_to_itself(self, admin):
        statuses = [status("a", is_initial=True, next_codes=["a"]), status("z", is_final=True)]
        assert (await admin.post(TYPES, json=type_body(statuses=statuses))).status_code == 422

    async def test_counts_as_committed_is_kept(self, admin):
        statuses = [
            status("a", is_initial=True),
            status("z", is_final=True, counts_as_committed=False),
        ]
        t = await create_type(admin, statuses=statuses)
        assert t["statuses"][1]["counts_as_committed"] is False

    async def test_duplicate_field_key_rejected(self, admin):
        fields = [field("x"), field("x", "Otro")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_invalid_field_key_rejected(self, admin):
        fields = [field("Bad Key")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_unknown_field_type_rejected(self, admin):
        fields = [field("x", type_="hologram")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_select_needs_options_or_catalog(self, admin):
        fields = [field("x", type_="select")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_select_with_own_options(self, admin):
        fields = [field("x", type_="select", options=[{"value": "a", "label": "A"}])]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 201

    async def test_select_with_options_and_catalog_rejected(self, admin):
        fields = [
            field("x", type_="select", options=[{"value": "a", "label": "A"}], catalog="airports")
        ]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_unknown_catalog_rejected(self, admin):
        fields = [field("x", type_="select", catalog="nope")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_text_field_cannot_carry_options(self, admin):
        fields = [field("x", options=[{"value": "a", "label": "A"}])]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_duplicate_option_values_rejected(self, admin):
        options = [{"value": "a", "label": "A"}, {"value": "a", "label": "B"}]
        fields = [field("x", type_="select", options=options)]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_two_fields_cannot_share_a_role(self, admin):
        fields = [
            field("a", type_="money", role="amount"),
            field("b", type_="money", role="amount"),
            field("c", type_="select", catalog="currencies", role="currency"),
        ]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_role_must_fit_the_field_type(self, admin):
        fields = [field("a", type_="text", role="amount")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_currency_needs_an_amount(self, admin):
        fields = [field("c", type_="select", catalog="currencies", role="currency")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_unit_needs_a_quantity(self, admin):
        fields = [field("u", type_="select", catalog="units", role="unit")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_unknown_role_rejected(self, admin):
        fields = [field("a", type_="text", role="boss")]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_all_roles_in_one_type(self, admin):
        fields = [
            field("amt", type_="money", role="amount"),
            field("cur", type_="select", catalog="currencies", role="currency"),
            field("day", type_="date", role="accounting_date"),
            field("plane", type_="text", role="aircraft"),
            field("what", type_="text", role="item"),
            field("qty", type_="number", role="quantity"),
            field("un", type_="select", catalog="units", role="unit"),
            field("who", type_="text", role="supplier"),
        ]
        t = await create_type(admin, fields=fields)
        assert {f["role"] for f in t["fields"]} == {
            "amount",
            "currency",
            "accounting_date",
            "aircraft",
            "item",
            "quantity",
            "unit",
            "supplier",
        }

    async def test_required_read_only_conflict(self, admin):
        fields = [field("x", required=True, read_only=True)]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_file_field_cannot_be_required(self, admin):
        fields = [field("f", type_="file", required=True)]
        assert (await admin.post(TYPES, json=type_body(fields=fields))).status_code == 422

    async def test_field_permissions_are_kept(self, admin):
        fields = [field("x", view_permission="audit.view", edit_permission="roles.manage")]
        t = await create_type(admin, fields=fields)
        assert t["fields"][0]["view_permission"] == "audit.view"
        assert t["fields"][0]["edit_permission"] == "roles.manage"

    async def test_unknown_extra_attribute_is_ignored_or_rejected_not_stored(self, admin):
        body = type_body()
        body["is_system"] = True
        r = await admin.post(TYPES, json=body)
        if r.status_code == 201:
            assert r.json()["is_system"] is False
        else:
            assert r.status_code == 422

    async def test_unknown_default_role_rejected(self, admin):
        body = type_body(default_role_id="00000000-0000-0000-0000-000000000000")
        assert (await admin.post(TYPES, json=body)).status_code == 422

    async def test_default_role_is_applied_to_new_tickets(self, admin):
        roles = (await admin.get(f"{API}/roles/?limit=100")).json()["data"]
        pilot_role = next(r for r in roles if r["code"] == "pilot")
        t = await create_type(admin, default_role_id=pilot_role["id"])
        r = await admin.post(
            f"{API}/tickets/",
            json={"type_code": t["code"], "title": "x", "payload": {"detail": "d"}},
        )
        assert r.json()["assigned_role_name"] == pilot_role["name"]

    async def test_steps_resolve_their_role(self, admin):
        roles = (await admin.get(f"{API}/roles/?limit=100")).json()["data"]
        pilot_role = next(r for r in roles if r["code"] == "pilot")
        steps = [{"code": "check", "name": "Revisar", "role_id": pilot_role["id"]}]
        t = await create_type(admin, steps=steps)
        assert t["steps"][0]["role_name"] == pilot_role["name"]

    async def test_step_with_unknown_role_rejected(self, admin):
        steps = [{"name": "Revisar", "role_id": "00000000-0000-0000-0000-000000000000"}]
        assert (await admin.post(TYPES, json=type_body(steps=steps))).status_code == 422

    async def test_duplicate_step_code_rejected(self, admin):
        steps = [{"code": "a", "name": "A"}, {"code": "a", "name": "B"}]
        assert (await admin.post(TYPES, json=type_body(steps=steps))).status_code == 422

    async def test_unknown_relation_kind_rejected(self, admin):
        assert (await admin.post(TYPES, json=type_body(relation_kinds=["ufo"]))).status_code == 422

    async def test_root_with_children(self, admin):
        t = await create_type(admin, is_root=True, child_type_codes=["expense"])
        assert t["child_type_codes"] == ["expense"]

    async def test_non_root_cannot_have_children(self, admin):
        r = await admin.post(TYPES, json=type_body(child_type_codes=["expense"]))
        assert r.status_code == 422

    async def test_unknown_child_type_rejected(self, admin):
        r = await admin.post(TYPES, json=type_body(is_root=True, child_type_codes=["ghost"]))
        assert r.status_code == 422

    async def test_root_type_cannot_be_a_child(self, admin):
        r = await admin.post(TYPES, json=type_body(is_root=True, child_type_codes=["operation"]))
        assert r.status_code == 422

    async def test_type_cannot_be_its_own_child(self, admin):
        body = type_body(is_root=True)
        body["child_type_codes"] = [body["code"]]
        assert (await admin.post(TYPES, json=body)).status_code == 422

    async def test_failed_create_leaves_no_permissions_behind(self, admin):
        body = type_body(fields=[])
        await admin.post(TYPES, json=body)
        codes = {p["code"] for p in (await admin.get(f"{API}/permissions/")).json()}
        assert f"tickets.view.{body['code']}" not in codes


class TestUpdate:
    async def test_rename(self, admin):
        t = await create_type(admin)
        r = await admin.patch(f"{TYPES}{t['id']}/", json={"name": "Nombre nuevo"})
        assert r.status_code == 200 and r.json()["name"] == "Nombre nuevo"

    async def test_code_cannot_change(self, admin):
        t = await create_type(admin)
        r = await admin.patch(f"{TYPES}{t['id']}/", json={"code": "otro_codigo"})
        assert r.status_code in (200, 422)
        assert (await admin.get(f"{TYPES}{t['id']}/")).json()["code"] == t["code"]

    async def test_is_system_cannot_be_set(self, admin):
        t = await create_type(admin)
        await admin.patch(f"{TYPES}{t['id']}/", json={"is_system": True})
        assert (await admin.get(f"{TYPES}{t['id']}/")).json()["is_system"] is False

    async def test_replace_statuses_keeps_the_ids_of_survivors(self, admin):
        t = await create_type(admin)
        before = {s["code"]: s["id"] for s in t["statuses"]}
        statuses = [
            status("open", "Abierto", is_initial=True),
            status("doing", "En curso"),
            status("done", "Hecho", is_final=True),
        ]
        r = await admin.patch(f"{TYPES}{t['id']}/", json={"statuses": statuses})
        after = {s["code"]: s["id"] for s in r.json()["statuses"]}
        assert after["open"] == before["open"] and after["done"] == before["done"]
        assert "doing" in after

    async def test_status_in_use_cannot_be_removed(self, admin):
        t = await create_type(admin)
        await admin.post(
            f"{API}/tickets/",
            json={"type_code": t["code"], "title": "x", "payload": {"detail": "d"}},
        )
        statuses = [status("fresh", is_initial=True), status("done", is_final=True)]
        r = await admin.patch(f"{TYPES}{t['id']}/", json={"statuses": statuses})
        assert r.status_code == 422
        kept = (await admin.get(f"{TYPES}{t['id']}/")).json()
        assert [s["code"] for s in kept["statuses"]] == ["open", "done"]

    async def test_replace_fields_swapping_a_role(self, admin):
        fields = [
            field("one", type_="money", role="amount"),
            field("two", type_="number"),
            field("cur", type_="select", catalog="currencies", role="currency"),
        ]
        t = await create_type(admin, fields=fields)
        swapped = [
            field("one", type_="number"),
            field("two", type_="money", role="amount"),
            field("cur", type_="select", catalog="currencies", role="currency"),
        ]
        r = await admin.patch(f"{TYPES}{t['id']}/", json={"fields": swapped})
        assert r.status_code == 200, r.text
        roles = {f["key"]: f["role"] for f in r.json()["fields"]}
        assert roles["two"] == "amount" and roles["one"] is None

    async def test_invalid_replacement_changes_nothing(self, admin):
        t = await create_type(admin)
        r = await admin.patch(
            f"{TYPES}{t['id']}/",
            json={"name": "Cambio parcial", "fields": [field("a"), field("a")]},
        )
        assert r.status_code == 422
        again = (await admin.get(f"{TYPES}{t['id']}/")).json()
        assert again["name"] == t["name"]

    async def test_root_flag_locked_once_tickets_exist(self, admin):
        t = await create_type(admin)
        await admin.post(
            f"{API}/tickets/",
            json={"type_code": t["code"], "title": "x", "payload": {"detail": "d"}},
        )
        r = await admin.patch(f"{TYPES}{t['id']}/", json={"is_root": True})
        assert r.status_code == 422

    async def test_system_type_cannot_be_deactivated(self, admin):
        t = await type_by_code(admin, "expense")
        r = await admin.patch(f"{TYPES}{t['id']}/", json={"is_active": False})
        assert r.status_code == 422

    async def test_deactivated_type_cannot_create_tickets(self, admin):
        t = await create_type(admin)
        await admin.patch(f"{TYPES}{t['id']}/", json={"is_active": False})
        r = await admin.post(
            f"{API}/tickets/",
            json={"type_code": t["code"], "title": "x", "payload": {"detail": "d"}},
        )
        assert r.status_code == 422

    async def test_rename_updates_permission_labels(self, admin):
        t = await create_type(admin)
        await admin.patch(f"{TYPES}{t['id']}/", json={"name": "Nombre renombrado"})
        perms = (await admin.get(f"{API}/permissions/")).json()
        view = next(p for p in perms if p["code"] == f"tickets.view.{t['code']}")
        assert "Nombre renombrado" in view["name"]

    async def test_update_is_audited_with_a_diff(self, admin):
        t = await create_type(admin)
        await admin.patch(f"{TYPES}{t['id']}/", json={"name": "Auditado ahora"})
        events = (await admin.get(f"{API}/audit/?entity=ticket_type&entity_id={t['id']}")).json()
        update = next(e for e in events["data"] if e["action"] == "update")
        assert any(c["field"] == "name" for c in update["changes"])

    async def test_unknown_id_is_404(self, admin):
        r = await admin.patch(f"{TYPES}00000000-0000-0000-0000-000000000000/", json={"name": "xxx"})
        assert r.status_code == 404


class TestDelete:
    async def test_deletes_an_unused_custom_type(self, admin):
        t = await create_type(admin)
        assert (await admin.delete(f"{TYPES}{t['id']}/")).status_code == 204
        assert (await admin.get(f"{TYPES}{t['id']}/")).status_code == 404

    async def test_delete_removes_derived_permissions(self, admin):
        t = await create_type(admin)
        await admin.delete(f"{TYPES}{t['id']}/")
        codes = {p["code"] for p in (await admin.get(f"{API}/permissions/")).json()}
        assert not any(c.endswith(f".{t['code']}") for c in codes)

    async def test_delete_removes_it_from_parents(self, admin):
        child = await create_type(admin)
        parent = await create_type(admin, is_root=True, child_type_codes=[child["code"]])
        await admin.delete(f"{TYPES}{child['id']}/")
        assert (await admin.get(f"{TYPES}{parent['id']}/")).json()["child_type_codes"] == []

    async def test_system_type_cannot_be_deleted(self, admin):
        t = await type_by_code(admin, "operation")
        assert (await admin.delete(f"{TYPES}{t['id']}/")).status_code == 422

    async def test_type_with_tickets_cannot_be_deleted(self, admin):
        t = await create_type(admin)
        await admin.post(
            f"{API}/tickets/",
            json={"type_code": t["code"], "title": "x", "payload": {"detail": "d"}},
        )
        assert (await admin.delete(f"{TYPES}{t['id']}/")).status_code == 422
        assert (await admin.get(f"{TYPES}{t['id']}/")).status_code == 200

    async def test_delete_unknown_is_404(self, admin):
        assert (
            await admin.delete(f"{TYPES}00000000-0000-0000-0000-000000000000/")
        ).status_code == 404


class TestAccess:
    @pytest.mark.parametrize("method", ["post", "patch", "delete"])
    async def test_pilot_cannot_manage_types(self, pilot, admin, method):
        t = await create_type(admin)
        url = TYPES if method == "post" else f"{TYPES}{t['id']}/"
        r = await pilot.request(method.upper(), url, json=type_body())
        assert r.status_code == 403

    @pytest.mark.parametrize("method", ["get", "post"])
    async def test_anonymous_gets_401(self, anon, method):
        assert (await anon.request(method.upper(), TYPES, json={})).status_code == 401

    async def test_operations_role_manages_types(self, ops):
        assert (await ops.post(TYPES, json=type_body())).status_code == 201

    async def test_new_type_is_usable_end_to_end(self, admin):
        t = await create_type(
            admin, is_root=True, child_type_codes=["expense"], relation_kinds=["aircraft"]
        )
        r = await admin.post(
            f"{API}/tickets/",
            json={"type_code": t["code"], "title": "raiz", "payload": {"detail": "d"}},
        )
        assert r.status_code == 201
        op = r.json()
        await make_expense(admin, op)


class TestIdempotentCreate:
    async def test_same_key_creates_one_type(self, admin):
        body, key = type_body(), new_key()
        a = await admin.post(TYPES, json=body, headers={"Idempotency-Key": key})
        b = await admin.post(TYPES, json=body, headers={"Idempotency-Key": key})
        assert a.status_code == b.status_code == 201
        assert a.json()["id"] == b.json()["id"]


async def test_make_operation_still_works(admin):
    assert (await make_operation(admin))["code"]
