import pytest

from tests.e2e.tickets_support import (
    get_ticket,
    history,
    make_expense,
    make_operation,
    patch,
    ticket_url,
)

ZERO = "00000000-0000-0000-0000-000000000000"


def reading(*fields):
    return {"fields": [{"key": k, "value": v, "confidence": c} for k, v, c in fields]}


DOUBTFUL = reading(("amount", 250, "low"), ("currency", "USD", "high"), ("concept", "fuel", "low"))


async def proposed(api, exp, body=None):
    r = await api.post(ticket_url(exp, "extractions/"), json=body or DOUBTFUL)
    assert r.status_code == 201, r.text
    return r.json()


async def blank_expense(api):
    return await make_expense(api, await make_operation(api), amount=10)


def approve(*pairs):
    return [{"key": k, "value": v} for k, v in pairs]


FULL = approve(("amount", 250), ("currency", "USD"), ("concept", "fuel"))


class TestReadingLifecycle:
    async def test_a_reading_is_stored_as_a_proposal(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        assert r["status"] == "extracted" and r["ticket_id"] == exp["id"]
        assert {f["key"] for f in r["fields"]} == {"amount", "currency", "concept"}
        assert next(f for f in r["fields"] if f["key"] == "amount")["label"] == "Monto"

    async def test_a_proposal_does_not_touch_the_ticket(self, accounting, admin):
        exp = await blank_expense(admin)
        await proposed(accounting, exp)
        again = await get_ticket(admin, exp)
        assert again["payload"]["amount"] == 10 and again["money"]["amount_base"] == 10

    async def test_listing_readings(self, accounting, admin):
        exp = await blank_expense(admin)
        r1 = await proposed(accounting, exp)
        rows = (await accounting.get(ticket_url(exp, "extractions/"))).json()
        assert [x["id"] for x in rows] == [r1["id"]]

    async def test_unknown_field_in_a_reading_is_rejected(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await accounting.post(
            ticket_url(exp, "extractions/"), json=reading(("ghost", "x", "high"))
        )
        assert r.status_code == 422

    async def test_file_field_in_a_reading_is_rejected(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await accounting.post(
            ticket_url(exp, "extractions/"), json=reading(("support", "x.pdf", "high"))
        )
        assert r.status_code == 422

    async def test_duplicate_field_in_a_reading_is_rejected(self, accounting, admin):
        exp = await blank_expense(admin)
        body = reading(("amount", 1, "high"), ("amount", 2, "high"))
        assert (
            await accounting.post(ticket_url(exp, "extractions/"), json=body)
        ).status_code == 422

    async def test_empty_reading_is_rejected(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await accounting.post(ticket_url(exp, "extractions/"), json={"fields": []})
        assert r.status_code == 422

    async def test_bad_confidence_is_rejected(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await accounting.post(
            ticket_url(exp, "extractions/"), json=reading(("amount", 1, "maybe"))
        )
        assert r.status_code == 422

    async def test_proposal_is_audited(self, accounting, admin):
        exp = await blank_expense(admin)
        await proposed(accounting, exp)
        assert any("lectura" in e["summary"] for e in await history(admin, exp))


class TestReviewGate:
    async def test_approving_blind_a_doubtful_value_is_rejected_naming_the_field(
        self, accounting, admin
    ):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        out = await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"), json={"fields": FULL}
        )
        assert out.status_code == 422
        assert "Monto" in out.json()["detail"] and "Concepto" in out.json()["detail"]
        assert {"amount", "concept"} <= out.json()["errors"].keys()

    async def test_marking_them_reviewed_allows_approval(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        out = await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": FULL, "confirmed": ["amount", "concept"]},
        )
        assert out.status_code == 200, out.text
        assert out.json()["payload"]["amount"] == 250
        assert out.json()["money"]["amount_base"] == 250

    async def test_correcting_a_value_counts_as_reviewing_it(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        fields = approve(("amount", 260), ("currency", "USD"), ("concept", "fuel"))
        out = await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": fields, "confirmed": ["concept"]},
        )
        assert out.status_code == 200
        assert out.json()["payload"]["amount"] == 260

    async def test_confirming_only_one_of_several_is_not_enough(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        out = await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": FULL, "confirmed": ["amount"]},
        )
        assert out.status_code == 422 and "concept" in out.json()["errors"]

    async def test_high_confidence_values_need_no_confirmation(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(
            accounting, exp, reading(("amount", 30, "high"), ("concept", "ice", "high"))
        )
        out = await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": approve(("amount", 30), ("concept", "ice"))},
        )
        assert out.status_code == 200 and out.json()["payload"]["concept"] == "ice"

    async def test_validating_without_sending_fields_keeps_the_proposal(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp, reading(("amount", 31, "high")))
        out = await accounting.post(ticket_url(exp, f"extractions/{r['id']}/validate/"), json={})
        assert out.status_code == 200 and out.json()["payload"]["amount"] == 31

    async def test_the_rejection_leaves_the_reading_pending_and_the_ticket_intact(
        self, accounting, admin
    ):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"), json={"fields": FULL}
        )
        rows = (await accounting.get(ticket_url(exp, "extractions/"))).json()
        assert rows[0]["status"] == "extracted"
        assert (await get_ticket(admin, exp))["payload"]["amount"] == 10

    async def test_a_validated_reading_cannot_be_validated_twice(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp, reading(("amount", 30, "high")))
        url = ticket_url(exp, f"extractions/{r['id']}/validate/")
        assert (await accounting.post(url, json={})).status_code == 200
        assert (await accounting.post(url, json={})).status_code == 422

    async def test_validation_records_who_and_what_was_corrected(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        fields = approve(("amount", 260), ("currency", "USD"), ("concept", "fuel"))
        await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": fields, "confirmed": ["concept"]},
        )
        done = (await accounting.get(ticket_url(exp, "extractions/"))).json()[0]
        assert done["status"] == "validated" and done["validated_by"] and done["validated_at"]
        assert done["corrections"] == [{"key": "amount", "from": 250, "to": 260}]

    async def test_the_correction_is_audited_with_both_values(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        fields = approve(("amount", 260), ("currency", "USD"), ("concept", "fuel"))
        await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": fields, "confirmed": ["concept"]},
        )
        event = next(
            e for e in await history(admin, exp) if "Validó el comprobante" in e["summary"]
        )
        change = next(c for c in event["changes"] if c["field"] == "amount")
        assert change["label"] == "Monto" and change["from"] == 250 and change["to"] == 260

    async def test_unknown_keys_in_the_validation_are_rejected(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp, reading(("amount", 30, "high")))
        out = await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": approve(("notes", "x"))},
        )
        assert out.status_code == 422

    async def test_confirming_an_unrelated_key_is_rejected(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp, reading(("amount", 30, "high")))
        out = await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"), json={"confirmed": ["notes"]}
        )
        assert out.status_code == 422

    async def test_an_invalid_corrected_value_is_rejected_and_nothing_changes(
        self, accounting, admin
    ):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp, reading(("amount", 30, "high")))
        out = await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": approve(("amount", "mucho"))},
        )
        assert out.status_code == 422
        assert (await get_ticket(admin, exp))["payload"]["amount"] == 10
        assert (await accounting.get(ticket_url(exp, "extractions/"))).json()[0][
            "status"
        ] == "extracted"

    async def test_validation_reevaluates_the_rules(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp, reading(("amount", 900, "high")))
        out = await accounting.post(ticket_url(exp, f"extractions/{r['id']}/validate/"), json={})
        assert any(h["rule_name"].startswith("Gasto mayor") for h in out.json()["rule_hits"])

    async def test_doubt_blocks_closing_until_validated(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp)
        blocked = await patch(admin, exp, status_code="verified")
        assert blocked.status_code == 422 and "baja confianza" in blocked.json()["detail"]
        await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": FULL, "confirmed": ["amount", "concept"]},
        )
        assert (await patch(admin, exp, status_code="verified")).status_code == 200

    async def test_closing_works_once_the_doubt_is_resolved(self, accounting, admin):
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp, reading(("amount", 10, "low")))
        await accounting.post(
            ticket_url(exp, f"extractions/{r['id']}/validate/"),
            json={"fields": approve(("amount", 10)), "confirmed": ["amount"]},
        )
        assert (await patch(admin, exp, status_code="verified")).status_code == 200

    async def test_a_high_confidence_pending_reading_does_not_block(self, accounting, admin):
        exp = await blank_expense(admin)
        await proposed(accounting, exp, reading(("amount", 10, "high")))
        assert (await patch(admin, exp, status_code="verified")).status_code == 200


class TestReadingAccess:
    async def test_other_tickets_reading_id_is_404(self, accounting, admin):
        a, b = await blank_expense(admin), await blank_expense(admin)
        r = await proposed(accounting, a, reading(("amount", 30, "high")))
        out = await accounting.post(ticket_url(b, f"extractions/{r['id']}/validate/"), json={})
        assert out.status_code == 404
        assert (await accounting.get(ticket_url(a, "extractions/"))).json()[0][
            "status"
        ] == "extracted"

    async def test_unknown_reading_is_404(self, accounting, admin):
        exp = await blank_expense(admin)
        out = await accounting.post(ticket_url(exp, f"extractions/{ZERO}/validate/"), json={})
        assert out.status_code == 404

    async def test_viewer_without_edit_cannot_propose(self, new_account, admin):
        api, _, _ = await new_account(["tickets.view"])
        exp = await blank_expense(admin)
        r = await api.post(ticket_url(exp, "extractions/"), json=reading(("amount", 1, "high")))
        assert r.status_code == 403

    async def test_viewer_without_edit_cannot_validate(self, new_account, accounting, admin):
        api, _, _ = await new_account(["tickets.view"])
        exp = await blank_expense(admin)
        r = await proposed(accounting, exp, reading(("amount", 30, "high")))
        out = await api.post(ticket_url(exp, f"extractions/{r['id']}/validate/"), json={})
        assert out.status_code == 403

    async def test_viewer_can_list_readings(self, new_account, accounting, admin):
        api, _, _ = await new_account(["tickets.view"])
        exp = await blank_expense(admin)
        await proposed(accounting, exp, reading(("amount", 30, "high")))
        assert len((await api.get(ticket_url(exp, "extractions/"))).json()) == 1

    async def test_reading_of_an_invisible_ticket_is_404(self, new_account, accounting, admin):
        api, _, _ = await new_account(["tickets.view.operation", "tickets.edit"])
        exp = await blank_expense(admin)
        assert (await api.get(ticket_url(exp, "extractions/"))).status_code == 404

    @pytest.mark.parametrize("method", ["get", "post"])
    async def test_anonymous_gets_401(self, anon, method):
        r = await anon.request(method.upper(), ticket_url(ZERO, "extractions/"), json={})
        assert r.status_code == 401
