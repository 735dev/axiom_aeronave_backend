"""Reconciliation (§6.18): the three differences, the period close and its immutability."""

import asyncio

from tests.e2e.attachments_support import attach, readable_png
from tests.e2e.closing_support import (
    acknowledge,
    audit_events,
    close_period,
    dated_expense,
    fresh_month,
    justify_all,
    report,
    validate_support,
    validated_expense,
)
from tests.e2e.conftest import API
from tests.e2e.tickets_support import get_ticket, make_expense, make_operation, patch, ticket_url


def kinds(rep: dict) -> list[str]:
    return sorted(d["kind"] for d in rep["differences"])


class TestDetection:
    async def test_matching_validated_receipt_is_reconciled(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin)
        await validated_expense(admin, op, 100, expense_date=day)
        rep = await report(admin, period)
        assert rep["differences"] == [] and len(rep["rows"]) == 1
        assert rep["rows"][0]["state"] == "reconciled"
        assert rep["totals"]["reconciled_base"] == 100

    async def test_an_empty_month_invents_nothing(self, admin):
        rep = await report(admin, fresh_month()[0])
        assert rep["rows"] == [] and rep["differences"] == []
        assert rep["open_differences"] == 0 and rep["close"] is None

    async def test_the_range_is_the_whole_month(self, admin):
        rep = await report(admin, "2020-02")
        assert rep["from"] == "2020-02-01" and rep["to"] == "2020-02-29"
        assert rep["base_currency"] == "USD"

    async def test_expense_without_any_file_is_high_severity(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        d = (await report(admin, period))["differences"]
        assert len(d) == 1
        assert d[0]["kind"] == "expense_without_support"
        assert d[0]["reason"] == "no_files" and d[0]["severity"] == "high"

    async def test_a_read_but_unvalidated_receipt_is_medium(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await attach(admin, expense, "r.png", content=readable_png())
        d = (await report(admin, period))["differences"]
        assert [(x["kind"], x["reason"], x["severity"]) for x in d] == [
            ("expense_without_support", "unvalidated", "medium")
        ]

    async def test_a_plain_file_is_support_enough(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await attach(admin, expense, "factura.pdf")
        assert (await report(admin, period))["differences"] == []

    async def test_amount_over_one_cent_is_a_mismatch(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await validate_support(admin, expense, 100)
        assert (await patch(admin, expense, payload={"amount": 100.02})).status_code == 200
        d = next(
            x
            for x in (await report(admin, period))["differences"]
            if x["kind"] == "amount_mismatch"
        )
        assert d["reason"] == "amount"
        assert d["expense_amount"] == 100.02 and d["receipt_amount"] == 100

    async def test_exactly_one_cent_is_tolerated(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await validate_support(admin, expense, 100)
        await patch(admin, expense, payload={"amount": 100.01})
        assert (await report(admin, period))["differences"] == []

    async def test_severity_threshold_is_ten_percent(self, admin):
        a, b = fresh_month(), fresh_month()
        op = await make_operation(admin)
        e1 = await dated_expense(admin, op, a[1])
        await validate_support(admin, e1, 100)
        await patch(admin, e1, payload={"amount": 105})
        e2 = await dated_expense(admin, op, b[1])
        await validate_support(admin, e2, 100)
        await patch(admin, e2, payload={"amount": 150})
        mid = (await report(admin, a[0]))["differences"][0]
        high = (await report(admin, b[0]))["differences"][0]
        assert mid["severity"] == "medium" and high["severity"] == "high"

    async def test_a_different_currency_is_a_currency_mismatch(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin, exchange_rate=36.5)
        expense = await dated_expense(admin, op, day)
        await validate_support(admin, expense, 100, "USD")
        assert (await patch(admin, expense, payload={"currency": "VES"})).status_code == 200
        d = next(
            x
            for x in (await report(admin, period))["differences"]
            if x["kind"] == "amount_mismatch"
        )
        assert d["reason"] == "currency"
        assert d["receipt_currency"] == "USD" and d["expense_currency"] == "VES"

    async def test_a_receipt_matching_no_expense_is_a_support_without_expense(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day, 137.37)
        await validate_support(admin, expense, 137.37)
        await patch(admin, expense, payload={"amount": 150.5})
        rep = await report(admin, period)
        assert kinds(rep) == ["amount_mismatch", "support_without_expense"]
        orphan = next(d for d in rep["differences"] if d["kind"] == "support_without_expense")
        assert orphan["reason"] == "no_matching_expense"

    async def test_a_sibling_expense_with_that_amount_adopts_the_receipt(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin)
        a = await dated_expense(admin, op, day, 137.37)
        await validate_support(admin, a, 137.37)
        await patch(admin, a, payload={"amount": 150.5})
        b = await dated_expense(admin, op, day, 137.37)
        await validate_support(admin, b, 137.37)
        rep = await report(admin, period)
        assert not [d for d in rep["differences"] if d["kind"] == "support_without_expense"]
        assert len([d for d in rep["differences"] if d["kind"] == "amount_mismatch"]) == 1

    async def test_a_receipt_on_a_rejected_expense_is_a_discarded_support(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await validate_support(admin, expense, 100)
        assert (await patch(admin, expense, status_code="rejected")).status_code == 200
        rep = await report(admin, period)
        assert len(rep["differences"]) == 1
        assert rep["differences"][0]["kind"] == "support_without_expense"
        assert rep["differences"][0]["reason"] == "discarded_expense"
        assert rep["rows"][0]["state"] == "difference"

    async def test_a_rejected_expense_without_receipt_is_just_discarded(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await patch(admin, expense, status_code="rejected")
        rep = await report(admin, period)
        assert rep["differences"] == [] and rep["rows"][0]["state"] == "discarded"
        assert rep["totals"]["discarded_base"] == 100 and rep["totals"]["reconciled_base"] == 0

    async def test_the_expense_lands_in_the_month_of_its_accounting_date(self, admin):
        period, day = fresh_month()
        other = fresh_month()[0]
        await dated_expense(admin, await make_operation(admin), day)
        assert len((await report(admin, period))["rows"]) == 1
        assert (await report(admin, other))["rows"] == []

    async def test_each_difference_has_a_stable_key(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        a = (await report(admin, period))["differences"][0]["key"]
        b = (await report(admin, period))["differences"][0]["key"]
        assert a == b

    async def test_rows_carry_what_the_csv_needs(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin)
        await dated_expense(admin, op, day)
        row = (await report(admin, period))["rows"][0]
        assert row["operation_code"] == op["code"] and row["date"] == day
        assert row["currency"] == "USD" and row["concept"] == "Combustible"

    async def test_foreign_currency_rows_report_the_frozen_base_amount(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin, exchange_rate=50)
        await dated_expense(admin, op, day, 5000, currency="VES")
        row = (await report(admin, period))["rows"][0]
        assert row["amount"] == 5000 and row["currency"] == "VES" and row["amount_base"] == 100

    async def test_the_totals_add_up_across_states(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin)
        await validated_expense(admin, op, 100, expense_date=day)
        await dated_expense(admin, op, day, 40)
        rep = await report(admin, period)
        assert rep["totals"]["reconciled_base"] == 100 and rep["totals"]["difference_base"] == 40

    async def test_a_receipt_pending_validation_does_not_count_as_a_receipt(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await attach(admin, expense, "r.png", content=readable_png())
        row = (await report(admin, period))["rows"][0]
        assert row["validated_receipts"] == 0 and row["receipt_amount"] is None


class TestPeriodValidation:
    async def test_bad_formats_are_422(self, admin):
        for bad in ("junio", "2020-13", "2020-00", "20-01", "2020-1", "2020/01"):
            r = await admin.get(f"{API}/reconciliation/?period={bad}")
            assert r.status_code == 422, bad

    async def test_missing_period_is_422(self, admin):
        assert (await admin.get(f"{API}/reconciliation/")).status_code == 422

    async def test_close_with_a_bad_period_is_422(self, admin):
        for bad in ("2020-13", "", "x", "2020-1-1"):
            assert (await close_period(admin, bad)).status_code == 422, bad

    async def test_a_future_period_cannot_be_closed(self, admin):
        r = await close_period(admin, "2999-01")
        assert r.status_code == 422 and r.json()["codigo"] == "periodo_futuro"

    async def test_extra_body_fields_are_ignored_never_applied(self, admin):
        period = fresh_month()[0]
        r = await admin.post(
            f"{API}/reconciliation/close/",
            json={"period": period, "closed_by_name": "Otro", "rows_count": 99, "total_base": 5},
        )
        assert r.status_code == 200
        close = r.json()["close"]
        assert close["closed_by_name"] != "Otro" and close["rows_count"] == 0
        assert close["total_base"] == 0


class TestJustification:
    async def test_a_short_note_is_422_with_codigo(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        r = await acknowledge(admin, period, key, "ok")
        assert r.status_code == 422 and r.json()["codigo"] == "justificacion"
        assert (await report(admin, period))["open_differences"] == 1

    async def test_a_whitespace_note_is_422(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        assert (await acknowledge(admin, period, key, "      ")).status_code == 422

    async def test_a_key_that_is_not_a_difference_is_404(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        assert (await acknowledge(admin, period, "x:y:z")).status_code == 404

    async def test_a_key_from_another_period_is_404(self, admin):
        a, b = fresh_month(), fresh_month()
        await dated_expense(admin, await make_operation(admin), a[1])
        key = (await report(admin, a[0]))["differences"][0]["key"]
        assert (await acknowledge(admin, b[0], key)).status_code == 404

    async def test_justifying_returns_the_report_with_who_and_why(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        r = await acknowledge(admin, period, key, "Se entrega mañana")
        assert r.status_code == 200
        body = r.json()
        ack = body["differences"][0]["acknowledgement"]
        assert ack["note"] == "Se entrega mañana" and ack["actor_name"] and ack["actor_role"]
        assert body["open_differences"] == 0

    async def test_justifying_again_replaces_the_note(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        await acknowledge(admin, period, key, "Primera nota")
        await acknowledge(admin, period, key, "Segunda nota")
        rep = await report(admin, period)
        assert rep["differences"][0]["acknowledgement"]["note"] == "Segunda nota"

    async def test_the_justification_shows_in_the_row(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        await justify_all(admin, period)
        assert (await report(admin, period))["rows"][0]["justification"] == "Justificación válida"

    async def test_justifying_is_audited_with_the_note(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        await acknowledge(admin, period, key, "Motivo auditable")
        events = await audit_events(admin, entity="reconciliation", entity_id=period)
        ev = next(e for e in events if "Justificó" in e["summary"])
        assert ev["changes"][0]["to"] == "Motivo auditable"

    async def test_a_note_over_the_limit_is_422(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        assert (await acknowledge(admin, period, key, "x" * 5000)).status_code == 422

    async def test_without_close_permission_it_is_403(self, ops, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        assert (await acknowledge(ops, period, key)).status_code == 403
        assert (await report(admin, period))["open_differences"] == 1


class TestClose:
    async def test_open_differences_block_the_close_and_name_their_keys(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        r = await close_period(admin, period)
        key = (await report(admin, period))["differences"][0]["key"]
        assert r.status_code == 422 and r.json()["codigo"] == "diferencias_abiertas"
        assert key in r.json()["errors"]
        assert (await report(admin, period))["close"] is None

    async def test_justified_differences_allow_the_close_and_leave_who_and_when(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        await justify_all(admin, period)
        r = await close_period(admin, period)
        assert r.status_code == 200
        close = r.json()["close"]
        assert close["period"] == period and close["rows_count"] == 1
        assert close["acknowledged_count"] == 1 and close["closed_by_name"]
        assert close["closed_at"] and close["total_base"] == 100
        assert (await report(admin, period))["close"]["closed_by_id"]

    async def test_a_clean_period_closes_directly(self, admin):
        period, day = fresh_month()
        await validated_expense(admin, await make_operation(admin), 100, expense_date=day)
        assert (await close_period(admin, period)).status_code == 200

    async def test_an_empty_past_period_closes(self, admin):
        assert (await close_period(admin, fresh_month()[0])).status_code == 200

    async def test_closing_twice_is_409_and_never_reopens(self, admin):
        period = fresh_month()[0]
        assert (await close_period(admin, period)).status_code == 200
        again = await close_period(admin, period)
        assert again.status_code == 409
        assert (await admin.delete(f"{API}/reconciliation/{period}/")).status_code in (404, 405)
        assert (await report(admin, period))["close"] is not None

    async def test_double_close_leaves_a_single_audit_entry(self, admin):
        period = fresh_month()[0]
        await close_period(admin, period)
        await close_period(admin, period)
        events = await audit_events(admin, entity="reconciliation", entity_id=period)
        assert len([e for e in events if "Cerró" in e["summary"]]) == 1

    async def test_two_concurrent_closes_close_once(self, admin):
        period = fresh_month()[0]
        a, b = await asyncio.gather(close_period(admin, period), close_period(admin, period))
        assert sorted([a.status_code, b.status_code]) == [200, 409]
        events = await audit_events(admin, entity="reconciliation", entity_id=period)
        assert len([e for e in events if "Cerró" in e["summary"]]) == 1

    async def test_a_justification_in_a_closed_period_is_409(self, admin):
        period = fresh_month()[0]
        await close_period(admin, period)
        assert (await acknowledge(admin, period, "a:b:c")).status_code == 409

    async def test_without_the_close_permission_it_is_403_and_nothing_closes(self, ops, admin):
        period = fresh_month()[0]
        assert (await close_period(ops, period)).status_code == 403
        assert (await report(admin, period))["close"] is None

    async def test_a_pilot_cannot_even_read_the_report(self, pilot):
        assert (await pilot.get(f"{API}/reconciliation/?period=2001-01")).status_code == 403

    async def test_a_blocked_close_leaves_no_close_event(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        await close_period(admin, period)
        events = await audit_events(admin, entity="reconciliation", entity_id=period)
        assert not [e for e in events if "Cerró" in e["summary"]]

    async def test_the_close_is_audited_with_actor_and_period(self, admin):
        period = fresh_month()[0]
        await close_period(admin, period)
        events = await audit_events(admin, entity="reconciliation", entity_id=period)
        ev = next(e for e in events if "Cerró" in e["summary"])
        assert ev["action"] == "update" and ev["actor_id"]

    async def test_the_period_list_shows_the_closed_ones(self, admin):
        period = fresh_month()[0]
        await close_period(admin, period)
        r = await admin.get(f"{API}/reconciliation/periods/")
        assert r.status_code == 200
        assert period in [p["period"] for p in r.json()]

    async def test_the_period_list_needs_analytics(self, pilot):
        assert (await pilot.get(f"{API}/reconciliation/periods/")).status_code == 403

    async def test_a_retry_with_the_same_idempotency_key_replays_the_answer(self, admin):
        period = fresh_month()[0]
        key = {"Idempotency-Key": f"close-{period}"}
        a = await admin.post(f"{API}/reconciliation/close/", json={"period": period}, headers=key)
        b = await admin.post(f"{API}/reconciliation/close/", json={"period": period}, headers=key)
        assert a.status_code == 200 and b.status_code == 200
        assert a.json()["close"] == b.json()["close"]


class TestImmutability:
    async def _closed(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin)
        expense = await dated_expense(admin, op, day)
        await validate_support(admin, expense, 100)
        assert (await close_period(admin, period)).status_code == 200
        return period, day, op, expense

    async def test_editing_a_closed_expense_is_409_and_changes_nothing(self, admin):
        _, _, _, e = await self._closed(admin)
        r = await patch(admin, e, payload={"amount": 999})
        assert r.status_code == 409 and r.json()["codigo"] == "periodo_cerrado"
        assert (await get_ticket(admin, e))["money"]["amount"] == 100

    async def test_changing_its_status_is_409(self, admin):
        _, _, _, e = await self._closed(admin)
        assert (await patch(admin, e, status_code="rejected")).status_code == 409

    async def test_the_denied_attempt_is_audited(self, admin):
        _, _, _, e = await self._closed(admin)
        await patch(admin, e, payload={"amount": 999})
        denied = await audit_events(admin, action="denied")
        hit = next(x for x in denied if x["entity_id"] == e["id"])
        assert "cerrado" in hit["summary"]

    async def test_a_file_cannot_be_attached_to_a_closed_period_expense(self, admin):
        _, _, _, e = await self._closed(admin)
        before = (await get_ticket(admin, e))["attachments"]
        r = await admin.post(
            ticket_url(e, "attachments/"),
            files={"file": ("t.pdf", b"%PDF-1.4\nz", "application/pdf")},
        )
        assert r.status_code == 409
        assert (await get_ticket(admin, e))["attachments"] == before

    async def test_a_pending_receipt_cannot_be_validated_after_the_close(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await attach(admin, expense, "r.png", content=readable_png())
        await justify_all(admin, period)
        assert (await close_period(admin, period)).status_code == 200
        files = (await admin.get(ticket_url(expense, "attachments/"))).json()
        fields = [{"key": f["key"], "value": f["value"]} for f in files[0]["extraction"]["fields"]]
        r = await admin.post(
            ticket_url(expense, f"attachments/{files[0]['id']}/validate/"),
            json={"fields": fields, "confirmed": [f["key"] for f in fields]},
        )
        assert r.status_code == 409

    async def test_a_new_expense_dated_in_the_closed_month_is_409(self, admin):
        _, day, op, _ = await self._closed(admin)
        r = await admin.post(
            f"{API}/tickets/",
            json={
                "type_code": "expense",
                "title": "Colado",
                "parent_id": op["id"],
                "payload": {"concept": "fuel", "amount": 5, "currency": "USD", "expense_date": day},
            },
        )
        assert r.status_code == 409 and r.json()["codigo"] == "periodo_cerrado"

    async def test_moving_an_open_expense_into_the_closed_month_is_409(self, admin):
        _, day, op, _ = await self._closed(admin)
        open_one = await dated_expense(admin, op, fresh_month()[1], 50)
        r = await patch(admin, open_one, payload={"expense_date": day})
        assert r.status_code == 409
        assert (await get_ticket(admin, open_one))["accounting_date"] != day

    async def test_another_month_stays_editable(self, admin):
        _, _, op, _ = await self._closed(admin)
        open_one = await dated_expense(admin, op, fresh_month()[1], 50)
        assert (await patch(admin, open_one, title="sigue editable")).status_code == 200

    async def test_moving_a_closed_expense_out_of_its_month_is_409(self, admin):
        _, _, _, e = await self._closed(admin)
        r = await patch(admin, e, payload={"expense_date": fresh_month()[1]})
        assert r.status_code == 409

    async def test_the_closed_report_does_not_change_after_a_rejected_edit(self, admin):
        period, _, _, e = await self._closed(admin)
        before = await report(admin, period)
        await patch(admin, e, payload={"amount": 999})
        assert (await report(admin, period))["rows"] == before["rows"]

    async def test_a_decision_on_a_closed_period_expense_is_409(self, admin):
        _, _, _, e = await self._closed(admin)
        r = await admin.post(
            ticket_url(e, "decisions/"), json={"step_code": "verify", "decision": "done"}
        )
        assert r.status_code == 409

    async def test_a_comment_on_a_closed_period_expense_is_409(self, admin):
        _, _, _, e = await self._closed(admin)
        assert (await patch(admin, e, comment="tarde")).status_code == 409

    async def test_a_concurrent_edit_and_close_stay_consistent(self, admin):
        period, day = fresh_month()
        expense = await dated_expense(admin, await make_operation(admin), day)
        await validate_support(admin, expense, 100)
        edit, close = await asyncio.gather(
            patch(admin, expense, payload={"amount": 100.01}), close_period(admin, period)
        )
        assert edit.status_code in (200, 409) and close.status_code in (200, 422)
        rep = await report(admin, period)
        if rep["close"] is not None:
            assert rep["close"]["total_base"] == sum(r["amount_base"] for r in rep["rows"])

    async def test_a_non_expense_ticket_is_never_locked_by_a_period(self, admin):
        await self._closed(admin)
        op = await make_operation(admin)
        assert (await patch(admin, op, title="libre")).status_code == 200


class TestScopeOfTheReport:
    async def test_a_viewer_of_operations_only_sees_no_expense_rows(self, new_account, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        viewer, _, _ = await new_account(["analytics.view", "tickets.view.operation"])
        rep = await report(viewer, period)
        assert rep["rows"] == [] and rep["differences"] == []

    async def test_a_viewer_of_expenses_sees_the_rows(self, new_account, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        viewer, _, _ = await new_account(["analytics.view", "tickets.view.expense"])
        assert len((await report(viewer, period))["rows"]) == 1

    async def test_the_close_is_not_narrowed_by_the_closers_view(self, new_account, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        closer, _, _ = await new_account(
            ["analytics.view", "reconciliation.close", "tickets.view.operation"]
        )
        r = await close_period(closer, period)
        assert r.status_code == 422 and r.json()["codigo"] == "diferencias_abiertas"
        assert (await report(admin, period))["close"] is None

    async def test_a_closer_who_sees_nothing_cannot_close_over_hidden_differences(
        self, new_account, admin
    ):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        closer, _, _ = await new_account(["analytics.view", "reconciliation.close"])
        assert (await close_period(closer, period)).status_code == 422

    async def test_the_report_leaks_nothing_in_errors(self, admin):
        r = await admin.get(f"{API}/reconciliation/?period=junio")
        assert "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()

    async def test_the_justifier_is_recorded_from_the_token_not_the_body(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        r = await admin.post(
            f"{API}/reconciliation/acknowledgements/",
            json={
                "period": period,
                "key": key,
                "note": "Nota suficiente",
                "actor_name": "Falso",
                "actor_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        ack = r.json()["differences"][0]["acknowledgement"]
        assert (
            ack["actor_name"] != "Falso"
            and ack["actor_id"] != "00000000-0000-0000-0000-000000000000"
        )
