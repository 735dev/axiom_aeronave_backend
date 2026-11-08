"""GET /tickets/{id}/closing/: the consolidated summary an operation is closed from."""

from tests.e2e.attachments_support import attach, files_of
from tests.e2e.closing_support import (
    closing,
    closing_url,
    decide,
    ready_operation,
    set_signature,
    sign,
    totals,
)
from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    children,
    get_ticket,
    make_expense,
    make_operation,
    patch,
)


async def with_support(api, op, **payload):
    expense = await make_expense(api, op, **payload)
    await attach(api, expense, "factura.pdf")
    return expense


class TestScope:
    async def test_unknown_id_is_404(self, ops):
        assert (
            await ops.get(closing_url("00000000-0000-0000-0000-000000000000"))
        ).status_code == 404

    async def test_not_a_uuid_is_422_or_404(self, ops):
        assert (await ops.get(f"{API}/tickets/nope/closing/")).status_code in (404, 422)

    async def test_a_child_ticket_is_404(self, ops):
        op = await make_operation(ops)
        expense = await make_expense(ops, op)
        assert (await ops.get(closing_url(expense))).status_code == 404

    async def test_operation_answers_200_with_its_code(self, ops):
        op = await make_operation(ops)
        body = await closing(ops, op)
        assert body["ticket_code"] == op["code"] and body["ticket_id"] == op["id"]
        assert body["closed"] is False and body["stamp"] is None

    async def test_requires_login(self, anon, ops):
        op = await make_operation(ops)
        assert (await anon.get(closing_url(op))).status_code == 401

    async def test_a_role_without_view_permission_is_403(self, new_account, ops):
        op = await make_operation(ops)
        outsider, _, _ = await new_account(["notifications.view"])
        assert (await outsider.get(closing_url(op))).status_code == 403

    async def test_a_role_that_cannot_view_operations_gets_404_not_data(self, new_account, ops):
        op = await make_operation(ops)
        only_expenses, _, _ = await new_account(["tickets.view.expense"])
        r = await only_expenses.get(closing_url(op))
        assert r.status_code == 404
        assert op["code"] not in r.text

    async def test_the_pilot_reads_it_like_any_viewer(self, pilot, ops):
        op = await make_operation(ops)
        assert (await pilot.get(closing_url(op))).status_code == 200

    async def test_error_body_has_no_traceback(self, ops):
        r = await ops.get(closing_url("00000000-0000-0000-0000-000000000000"))
        assert "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()


class TestProgressByRole:
    async def test_totals_add_up_the_steps_of_the_operation_and_its_children(self, ops):
        op = await make_operation(ops)
        body = await closing(ops, op)
        assert sum(r["total"] for r in body["progress"]) > 0
        assert sum(r["done"] for r in body["progress"]) == 0

    async def test_a_registered_step_moves_from_pending_to_done(self, ops):
        op = await make_operation(ops)
        row = lambda b: next(
            r for r in b["progress"] if r["role_name"] == "Gerencia de Operaciones"
        )
        before = row(await closing(ops, op))
        assert (await decide(ops, op, "planning")).status_code == 201
        after = row(await closing(ops, op))
        assert after["done"] == before["done"] + 1
        assert any("Planificación" in p for p in before["pending"])
        assert not any("Planificación" in p for p in after["pending"])
        assert any("Firma de cierre" in p for p in after["pending"])

    async def test_pending_items_name_the_ticket_they_belong_to(self, ops):
        op = await make_operation(ops)
        report = (await children(ops, op, type_code="technical_report"))[0]
        pending = [p for r in (await closing(ops, op))["progress"] for p in r["pending"]]
        assert any(p.startswith(f"{report['code']} ·") for p in pending)

    async def test_rows_carry_role_id_and_name(self, ops):
        body = await closing(ops, await make_operation(ops))
        assert all(r["role_name"] and "total" in r for r in body["progress"])

    async def test_done_never_exceeds_total(self, ops):
        op = await ready_operation(ops)
        assert all(r["done"] <= r["total"] for r in (await closing(ops, op))["progress"])


class TestExpensesAndTotals:
    async def test_totals_equal_the_totals_endpoint_for_the_operation(self, ops):
        op = await make_operation(ops, exchange_rate=50)
        await make_expense(ops, op, amount=100, currency="USD")
        await make_expense(ops, op, amount=5000, currency="VES")
        mine = (await closing(ops, op))["totals"]
        other = await totals(ops, parent_id=op["id"])
        assert mine == other
        assert sorted(c["currency"] for c in mine["by_currency"]) == ["USD", "VES"]

    async def test_every_expense_is_listed_with_support_flag_and_base(self, ops):
        op = await make_operation(ops)
        a = await with_support(ops, op, amount=100)
        b = await make_expense(ops, op, amount=40)
        rows = (await closing(ops, op))["expenses"]
        assert len(rows) == 2
        ra = next(r for r in rows if r["code"] == a["code"])
        rb = next(r for r in rows if r["code"] == b["code"])
        assert ra["has_support"] is True and rb["has_support"] is False
        assert ra["amount"] == 100 and ra["currency"] == "USD" and ra["concept"]
        assert ra["amount_base"] == a["money"]["amount_base"]

    async def test_concept_is_the_catalog_label_not_the_code(self, ops):
        op = await make_operation(ops)
        await make_expense(ops, op, concept="fuel")
        assert (await closing(ops, op))["expenses"][0]["concept"] == "Combustible"

    async def test_confirmed_and_in_progress_are_separated(self, ops):
        op = await make_operation(ops)
        a = await make_expense(ops, op, amount=100)
        b = await make_expense(ops, op, amount=30)
        assert (await patch(ops, a, status_code="verified")).status_code == 200
        body = await closing(ops, op)
        assert body["confirmed"]["base"] == a["money"]["amount_base"]
        assert body["in_progress"]["base"] == b["money"]["amount_base"]
        assert body["confirmed"]["by_currency"][0]["amount"] == 100
        assert body["in_progress"]["by_currency"][0]["amount"] == 30

    async def test_a_rejected_expense_counts_for_neither(self, ops):
        op = await make_operation(ops)
        a = await make_expense(ops, op, amount=100)
        assert (await patch(ops, a, status_code="rejected")).status_code == 200
        body = await closing(ops, op)
        assert body["confirmed"]["base"] + body["in_progress"]["base"] == 0
        assert body["expenses"][0]["counts"] is False
        assert body["totals"]["excluded_base"] > 0

    async def test_other_operations_expenses_do_not_leak_in(self, ops):
        a = await make_operation(ops)
        b = await make_operation(ops)
        await make_expense(ops, b, amount=777)
        assert (await closing(ops, a))["expenses"] == []

    async def test_an_empty_operation_has_zero_money(self, ops):
        body = await closing(ops, await make_operation(ops))
        assert body["totals"]["by_currency"] == [] and body["confirmed"]["base"] == 0


class TestSupportInventory:
    async def test_files_of_the_operation_and_its_children_are_consolidated(self, ops):
        op = await make_operation(ops)
        expense = await make_expense(ops, op)
        await attach(ops, op, "manifiesto.pdf", field_key="manifest")
        await attach(ops, expense, "factura.pdf")
        inventory = (await closing(ops, op))["supports"]
        assert sorted(i["ticket_code"] for i in inventory) == sorted([op["code"], expense["code"]])
        assert all(i["filename"] and i["folder"] and i["uploaded_by"] for i in inventory)

    async def test_counts_by_document_type(self, ops):
        op = await make_operation(ops)
        e1, e2 = await make_expense(ops, op), await make_expense(ops, op)
        await attach(ops, e1, "a.pdf")
        await attach(ops, e2, "b.pdf")
        await attach(ops, op, "m.pdf", field_key="manifest")
        by = {x["document_type"]: x["count"] for x in (await closing(ops, op))["supports_by_type"]}
        assert by["comprobante"] == 2 and by["manifiesto"] == 1

    async def test_files_of_another_operation_are_not_mixed(self, ops):
        a, b = await make_operation(ops), await make_operation(ops)
        await attach(ops, await make_expense(ops, b), "x.pdf")
        assert (await closing(ops, a))["supports"] == []

    async def test_a_removed_file_leaves_the_inventory(self, ops):
        op = await make_operation(ops)
        expense = await make_expense(ops, op)
        await attach(ops, expense, "x.pdf")
        file = (await files_of(ops, expense))[0]
        r = await ops.delete(f"{API}/tickets/{expense['id']}/attachments/{file['id']}/")
        assert r.status_code == 200, r.text
        assert (await closing(ops, op))["supports"] == []

    async def test_inventory_never_carries_download_links_or_bytes(self, ops):
        op = await make_operation(ops)
        await attach(ops, await make_expense(ops, op), "x.pdf")
        text = (await ops.get(closing_url(op))).text
        assert "data:" not in text and "http" not in text


class TestBlockers:
    async def test_unresolved_child_and_pending_steps_are_listed(self, ops):
        op = await make_operation(ops)
        report = (await children(ops, op, type_code="technical_report"))[0]
        body = await closing(ops, op)
        assert any(
            b["kind"] == "unresolved_child" and b["ticket_code"] == report["code"]
            for b in body["blockers"]
        )
        assert any(
            b["kind"] == "pending_step" and b["step_code"] == "planning" for b in body["blockers"]
        )
        assert body["ready"] is False

    async def test_an_expense_without_support_blocks_and_points_at_it(self, ops):
        op = await ready_operation(ops)
        expense = await make_expense(ops, op)
        body = await closing(ops, op)
        assert any(
            b["kind"] == "expense_without_support" and b["ticket_id"] == expense["id"]
            for b in body["blockers"]
        )
        assert body["ready"] is False

    async def test_a_read_but_unvalidated_receipt_blocks(self, ops):
        from tests.e2e.attachments_support import readable_png

        op = await ready_operation(ops)
        expense = await make_expense(ops, op)
        await attach(ops, expense, "recibo.png", content=readable_png())
        body = await closing(ops, op)
        assert any(b["kind"] == "receipt_awaiting_validation" for b in body["blockers"])
        assert body["ready"] is False

    async def test_only_the_signature_left_means_ready(self, ops):
        op = await ready_operation(ops)
        await with_support(ops, op)
        body = await closing(ops, op)
        assert [b["step_code"] for b in body["blockers"]] == ["signature"]
        assert body["ready"] is True

    async def test_signing_clears_the_last_blocker(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        assert (await sign(ops, op)).status_code == 201
        body = await closing(ops, op)
        assert body["blockers"] == [] and body["ready"] is True

    async def test_a_rejected_expense_needs_no_support(self, ops):
        op = await ready_operation(ops)
        expense = await make_expense(ops, op)
        assert (await patch(ops, expense, status_code="rejected")).status_code == 200
        assert not any(
            b["kind"] == "expense_without_support" for b in (await closing(ops, op))["blockers"]
        )

    async def test_blockers_are_precise_one_entry_per_expense(self, ops):
        op = await ready_operation(ops)
        a, b = await make_expense(ops, op), await make_expense(ops, op)
        ids = [
            x["ticket_id"]
            for x in (await closing(ops, op))["blockers"]
            if x["kind"] == "expense_without_support"
        ]
        assert sorted(ids) == sorted([a["id"], b["id"]])


class TestSigner:
    async def test_reports_whether_the_user_has_a_signature(self, ops):
        op = await make_operation(ops)
        await set_signature(ops, "")
        assert (await closing(ops, op))["signer"]["has_signature"] is False
        await set_signature(ops)
        assert (await closing(ops, op))["signer"]["has_signature"] is True

    async def test_who_can_sign(self, ops, admin, pilot):
        op = await make_operation(ops)
        assert (await closing(ops, op))["signer"]["can_sign"] is True
        assert (await closing(admin, op))["signer"]["can_sign"] is True
        assert (await closing(pilot, op))["signer"]["can_sign"] is False

    async def test_signer_names_the_step_and_the_role_that_signs(self, ops):
        signer = (await closing(ops, await make_operation(ops)))["signer"]
        assert signer["step_code"] == "signature"
        assert signer["step_role_name"] == "Gerencia de Operaciones"
        assert signer["step_signed"] is False

    async def test_step_signed_flips_after_the_signature(self, ops):
        await set_signature(ops)
        op = await make_operation(ops)
        await sign(ops, op)
        assert (await closing(ops, op))["signer"]["step_signed"] is True

    async def test_signing_without_a_stored_signature_does_not_count_as_signed(self, ops):
        await set_signature(ops, "")
        op = await make_operation(ops)
        assert (await sign(ops, op)).status_code == 201
        assert (await closing(ops, op))["signer"]["step_signed"] is False

    async def test_the_summary_never_exposes_the_users_signature_image(self, ops):
        await set_signature(ops)
        op = await make_operation(ops)
        assert "data:image" not in (await ops.get(closing_url(op))).text
