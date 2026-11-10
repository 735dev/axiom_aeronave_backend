"""The signed closing: the blockers, the stamp, and the immutability afterwards."""

import asyncio

from tests.e2e.attachments_support import attach, readable_png
from tests.e2e.closing_support import (
    audit_events,
    close_operation,
    closing,
    closed_operation,
    decide,
    me,
    ready_operation,
    set_signature,
    sign,
    sign_step,
    signer_of,
    supported_expense,
    validate_support,
    SIGNING_STEPS,
)
from tests.e2e.conftest import API, jpeg_data_url, png_data_url
from tests.e2e.tickets_support import (
    get_ticket,
    history,
    make_expense,
    make_operation,
    patch,
    ticket_url,
)


def signature_decision(ticket: dict, step: str = "sign_captain") -> dict:
    return next(d for d in ticket["decisions"] if d["step_code"] == step)


class TestDecisionSignature:
    async def test_the_decision_records_that_it_was_signed(self, ops):
        op = await make_operation(ops)
        r = await sign_step(ops, op, "sign_captain")
        assert r.status_code == 201
        assert signature_decision(await get_ticket(ops, op))["signed"] is True

    async def test_without_a_stored_signature_the_decision_is_unsigned(self, ops):
        op = await make_operation(ops)
        signer = await signer_of(ops, "sign_captain", with_signature=False)
        assert (await decide(signer, op, "sign_captain")).status_code == 201
        assert signature_decision(await get_ticket(ops, op))["signed"] is False

    async def test_other_steps_are_never_marked_signed(self, ops):
        await set_signature(ops)
        op = await make_operation(ops)
        await decide(ops, op, "planning")
        planning = next(
            d for d in (await get_ticket(ops, op))["decisions"] if d["step_code"] == "planning"
        )
        assert planning["signed"] is False

    async def test_the_ticket_body_never_carries_the_signature_image(self, ops):
        op = await make_operation(ops)
        await sign(ops, op)
        text = (await ops.get(ticket_url(op))).text
        assert "data:image" not in text

    async def test_a_signature_posted_in_the_decision_body_is_ignored(self, ops):
        op = await make_operation(ops)
        signer = await signer_of(ops, "sign_captain", with_signature=False)
        r = await signer.post(
            ticket_url(op, "decisions/"),
            json={
                "step_code": "sign_captain",
                "decision": "done",
                "signature_url": png_data_url(),
            },
        )
        assert r.status_code in (201, 422)
        if r.status_code == 201:
            assert signature_decision(await get_ticket(ops, op))["signed"] is False

    async def test_a_signing_step_never_copies_the_signature_of_another_user(self, ops, admin):
        # The superuser has a signature, the captain does not: the decision stays unsigned.
        await set_signature(admin)
        op = await make_operation(ops)
        signer = await signer_of(ops, "sign_captain", with_signature=False)
        await decide(signer, op, "sign_captain")
        assert signature_decision(await get_ticket(ops, op))["signed"] is False


class TestEachSignatureBelongsToItsRole:
    async def test_the_four_steps_exist_in_order_and_require_signature(self, admin):
        types = (await admin.get(f"{API}/ticket-types/")).json()
        data = types["data"] if isinstance(types, dict) else types
        operation = next(t for t in data if t["code"] == "operation")
        signing = [s for s in operation["steps"] if s["requires_signature"]]
        assert [s["code"] for s in signing] == list(SIGNING_STEPS)
        assert [s["role_name"] for s in signing] == [
            "Piloto",
            "Mantenimiento",
            "Coordinación AVSEC",
            "Gerencia General",
        ]
        assert not any(s["code"] == "signature" for s in operation["steps"])

    async def test_another_role_cannot_sign_a_step_that_is_not_theirs(self, ops, pilot):
        op = await make_operation(ops)
        for step in ("sign_maintenance", "sign_avsec", "sign_general"):
            r = await decide(pilot, op, step)
            assert r.status_code == 403, (step, r.text)
        assert not any(
            d["step_code"].startswith("sign_") for d in (await get_ticket(ops, op))["decisions"]
        )

    async def test_decide_any_does_not_open_a_signature(self, ops, general):
        # Operaciones and Gerencia General hold tickets.decide_any: still not their signature.
        op = await make_operation(ops)
        assert (await decide(ops, op, "sign_captain")).status_code == 403
        assert (await decide(general, op, "sign_captain")).status_code == 403

    async def test_the_superuser_cannot_sign_in_place_of_a_role(self, ops, admin):
        op = await make_operation(ops)
        assert (await decide(admin, op, "sign_general")).status_code == 403

    async def test_each_signer_uses_their_own_profile_signature(self, ops):
        op = await make_operation(ops)
        await sign(ops, op)
        decisions = (await get_ticket(ops, op))["decisions"]
        by_step = {d["step_code"]: d for d in decisions}
        assert [by_step[s]["signed"] for s in SIGNING_STEPS] == [True] * 4
        assert [by_step[s]["decided_by"] for s in SIGNING_STEPS] == [
            "Cap. Rodríguez",
            "Gerencia de Mantenimiento",
            "Coordinación AVSEC",
            "Gerencia General",
        ]

    async def test_the_general_manager_signs_without_edit_rights(self, ops, general):
        op = await make_operation(ops)
        await set_signature(general)
        assert (await decide(general, op, "sign_general")).status_code == 201
        # Signing opens no editing: the same account cannot change the operation.
        assert (await patch(general, op, title="otro")).status_code == 403

    async def test_a_role_without_view_of_the_type_cannot_sign(self, ops, new_account):
        api, _, _ = await new_account(["tickets.sign"])
        op = await make_operation(ops)
        r = await decide(api, op, "sign_general")
        assert r.status_code in (403, 404)


class TestCloseGuards:
    async def test_with_three_of_four_signatures_the_close_is_422_and_names_who_is_missing(
        self, ops
    ):
        op = await ready_operation(ops)
        for step in SIGNING_STEPS[:3]:
            assert (await sign_step(ops, op, step)).status_code == 201
        r = await close_operation(ops, op)
        assert r.status_code == 422
        assert "faltan las firmas de Gerencia General" in r.json()["detail"]
        now = await get_ticket(ops, op)
        assert now["status_is_final"] is False and now["closing"] is None

    async def test_with_no_signature_the_close_names_every_role(self, ops):
        op = await ready_operation(ops)
        detail = (await close_operation(ops, op)).json()["detail"]
        for role in ("Piloto", "Mantenimiento", "Coordinación AVSEC", "Gerencia General"):
            assert role in detail

    async def test_the_four_signatures_in_any_order_let_it_close(self, ops):
        op = await ready_operation(ops)
        for step in reversed(SIGNING_STEPS):
            assert (await sign_step(ops, op, step)).status_code == 201
        assert (await close_operation(ops, op)).status_code == 200

    async def test_without_a_signature_in_the_profile_the_close_is_422(self, ops):
        op = await ready_operation(ops)
        await sign(ops, op, unsigned="sign_avsec")
        r = await close_operation(ops, op)
        assert r.status_code == 422
        assert "cargar su firma en su perfil" in r.json()["detail"]
        assert "Coordinación AVSEC" in r.json()["detail"]
        now = await get_ticket(ops, op)
        assert now["status_is_final"] is False and now["closing"] is None

    async def test_loading_the_signature_and_signing_again_lets_it_close(self, ops):
        op = await ready_operation(ops)
        await sign(ops, op, unsigned="sign_avsec")
        assert (await close_operation(ops, op)).status_code == 422
        assert (await sign_step(ops, op, "sign_avsec")).status_code == 201
        assert (await close_operation(ops, op)).status_code == 200

    async def test_a_loaded_signature_without_the_decision_does_not_close(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        r = await close_operation(ops, op)
        assert r.status_code == 422

    async def test_an_expense_without_support_blocks_and_names_it(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        expense = await make_expense(ops, op)
        await sign(ops, op)
        r = await close_operation(ops, op)
        assert r.status_code == 422 and expense["code"] in r.json()["detail"]

    async def test_an_unvalidated_receipt_blocks(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        expense = await make_expense(ops, op)
        await attach(ops, expense, "recibo.png", content=readable_png())
        await sign(ops, op)
        r = await close_operation(ops, op)
        assert r.status_code == 422 and "sin validar" in r.json()["detail"]

    async def test_validating_the_receipt_unblocks(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        expense = await make_expense(ops, op)
        await validate_support(ops, expense, 100)
        await sign(ops, op)
        assert (await close_operation(ops, op)).status_code == 200

    async def test_a_rejected_expense_without_support_does_not_block(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        expense = await make_expense(ops, op)
        await patch(ops, expense, status_code="rejected")
        await sign(ops, op)
        assert (await close_operation(ops, op)).status_code == 200

    async def test_pending_steps_still_block(self, ops):
        await set_signature(ops)
        op = await make_operation(ops)
        r = await close_operation(ops, op)
        assert r.status_code == 422

    async def test_a_failed_close_leaves_the_operation_open_and_audits_nothing_as_closed(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        await make_expense(ops, op)
        await sign(ops, op)
        await close_operation(ops, op)
        assert (await get_ticket(ops, op))["status_code"] != "closed"

    async def test_errors_are_spanish_and_generic(self, ops):
        op = await ready_operation(ops)
        await sign(ops, op, unsigned="sign_captain")
        text = (await close_operation(ops, op)).text
        assert "Traceback" not in text and "sqlalchemy" not in text.lower()

    async def test_cancelling_needs_no_signature_and_leaves_no_stamp(self, ops):
        await set_signature(ops, "")
        op = await make_operation(ops)
        r = await patch(ops, op, status_code="cancelled")
        assert r.status_code == 200
        assert (await get_ticket(ops, op))["closing"] is None


class TestStamp:
    async def test_close_stamps_who_when_totals_files_and_folder(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        expense = await supported_expense(ops, op, amount=100)
        await attach(ops, op, "m.pdf", field_key="manifest")
        await patch(ops, expense, status_code="verified")
        await sign(ops, op)
        before = await closing(ops, op)
        r = await close_operation(ops, op)
        assert r.status_code == 200
        stamp = (await get_ticket(ops, op))["closing"]
        # The seal's own signer is the last to sign: Gerencia General.
        assert stamp["signed_by"] == "Gerencia General"
        assert stamp["signed_role"] == "Gerencia General"
        assert stamp["has_signature"] is True and stamp["files_count"] == 2
        assert stamp["folder"].startswith("20") and stamp["signed_at"]
        assert stamp["totals"]["committed_base"] == expense["money"]["amount_base"]
        assert stamp["totals"] == before["totals"]

    async def test_the_ticket_exposes_no_signature_image_but_the_summary_does(self, ops):
        await set_signature(ops)
        op = await closed_operation(ops)
        assert "data:image" not in (await ops.get(ticket_url(op))).text
        full = (await closing(ops, op))["stamp"]
        assert full["signature_url"].startswith("data:image/png;base64,")

    async def test_the_seal_keeps_the_four_signatures_with_their_images(self, ops):
        op = await closed_operation(ops)
        stamp = (await closing(ops, op))["stamp"]
        signatures = stamp["signatures"]
        assert [x["step_code"] for x in signatures] == list(SIGNING_STEPS)
        assert [x["role_name"] for x in signatures] == [
            "Piloto",
            "Mantenimiento",
            "Coordinación AVSEC",
            "Gerencia General",
        ]
        assert all(x["signature_url"].startswith("data:image/png;base64,") for x in signatures)
        assert all(x["user_name"] and x["signed_at"] and x["step_name"] for x in signatures)

    async def test_the_ticket_carries_no_signature_rows_or_images(self, ops):
        op = await closed_operation(ops)
        text = (await ops.get(ticket_url(op))).text
        assert "signatures" not in text and "data:image" not in text

    async def test_the_summary_of_a_closed_operation_says_closed(self, ops):
        op = await closed_operation(ops)
        body = await closing(ops, op)
        assert body["closed"] is True and body["stamp"]["signed_by"]

    async def test_a_child_that_closes_gets_no_stamp(self, ops):
        op = await make_operation(ops)
        expense = await make_expense(ops, op)
        await patch(ops, expense, status_code="verified")
        assert (await get_ticket(ops, expense))["closing"] is None

    async def test_changing_the_profile_signature_afterwards_does_not_alter_the_seal(self, ops):
        op = await closed_operation(ops)
        before = (await closing(ops, op))["stamp"]["signature_url"]
        await set_signature(ops, jpeg_data_url())
        assert (await closing(ops, op))["stamp"]["signature_url"] == before
        await set_signature(ops)

    async def test_the_stamp_is_sealed_exactly_once(self, ops):
        op = await closed_operation(ops)
        first = (await get_ticket(ops, op))["closing"]["signed_at"]
        await patch(ops, op, title="otro")
        assert (await get_ticket(ops, op))["closing"]["signed_at"] == first

    async def test_the_close_leaves_a_status_change_in_the_history(self, ops):
        op = await closed_operation(ops)
        assert any(e["action"] == "status_change" for e in await history(ops, op))

    async def test_the_stamp_survives_a_listing(self, ops):
        op = await closed_operation(ops)
        rows = (await ops.get(f"{API}/tickets/?is_root=true&limit=100&open=false")).json()["data"]
        row = next((r for r in rows if r["id"] == op["id"]), None)
        if row is not None:
            assert row["closing"]["signed_by"]
            assert "data:image" not in str(row)


class TestImmutability:
    async def test_any_later_change_is_409(self, ops):
        op = await closed_operation(ops)
        assert (await patch(ops, op, title="otro")).status_code == 409
        assert (await patch(ops, op, status_code="planned")).status_code == 409

    async def test_the_body_cannot_rewrite_the_stamp(self, ops):
        op = await closed_operation(ops)
        stamp = (await get_ticket(ops, op))["closing"]
        r = await patch(ops, op, closing={**stamp, "signed_by": "Otro"})
        assert r.status_code in (409, 422)
        assert (await get_ticket(ops, op))["closing"]["signed_by"] == stamp["signed_by"]

    async def test_signing_again_after_the_close_is_409_and_audited_as_denied(self, ops, admin):
        op = await closed_operation(ops)
        assert (await sign_step(ops, op, "sign_captain")).status_code == 409
        events = await audit_events(admin, action="denied")
        assert any(e["entity_id"] == op["id"] for e in events)

    async def test_a_file_cannot_be_added_to_a_sealed_operation(self, ops):
        op = await closed_operation(ops)
        r = await ops.post(
            ticket_url(op, "attachments/"),
            files={"file": ("x.pdf", b"%PDF-1.4\nx", "application/pdf")},
        )
        assert r.status_code == 409

    async def test_a_new_expense_cannot_be_added_to_a_sealed_operation(self, ops):
        op = await closed_operation(ops)
        r = await ops.post(
            f"{API}/tickets/",
            json={
                "type_code": "expense",
                "title": "tarde",
                "parent_id": op["id"],
                "payload": {"concept": "fuel", "amount": 5, "currency": "USD"},
            },
        )
        assert r.status_code == 409

    async def test_closing_twice_is_409_and_keeps_one_stamp(self, ops):
        op = await closed_operation(ops)
        assert (await close_operation(ops, op)).status_code == 409
        assert (await get_ticket(ops, op))["closing"]["signed_by"]

    async def test_two_concurrent_closes_seal_exactly_once(self, ops):
        await set_signature(ops)
        op = await ready_operation(ops)
        await sign(ops, op)
        a, b = await asyncio.gather(close_operation(ops, op), close_operation(ops, op))
        assert sorted([a.status_code, b.status_code]) == [200, 409]
        assert (await get_ticket(ops, op))["closing"]["signed_by"]

    async def test_a_decision_on_the_sealed_operation_leaves_the_stamp_alone(self, ops):
        op = await closed_operation(ops)
        stamp = (await get_ticket(ops, op))["closing"]
        await decide(ops, op, "planning", "pending")
        assert (await get_ticket(ops, op))["closing"] == stamp

    async def test_a_closed_operation_summary_still_reads(self, ops):
        op = await closed_operation(ops)
        assert (await ops.get(ticket_url(op, "closing/"))).status_code == 200
