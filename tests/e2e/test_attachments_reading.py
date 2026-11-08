"""Receipt reading: preview endpoint, reading on upload, validation gate and pending queue."""

from __future__ import annotations

import pytest

from tests.e2e.attachments_support import (
    attach,
    extract,
    new_expense,
    pdf_bytes,
    png_bytes,
    readable_png,
    unreadable_png,
    upload,
)
from tests.e2e.conftest import API
from tests.e2e.tickets_support import get_ticket, make_operation


class TestExtractPreview:
    async def test_readable_receipt_proposes_fields_with_confidence(self, admin):
        r = await extract(admin, "expense", "r.png", readable_png())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "extracted"
        assert body["fields"]
        for f in body["fields"]:
            assert f["confidence"] in ("high", "low")
            assert f["key"] and "value" in f

    async def test_includes_amount_and_currency(self, admin):
        body = (await extract(admin, "expense", "r.png", readable_png())).json()
        keys = {f["key"] for f in body["fields"]}
        assert {"amount", "currency"} <= keys

    async def test_is_repeatable(self, admin):
        content = readable_png()
        a = (await extract(admin, "expense", "r.png", content)).json()
        b = (await extract(admin, "expense", "r.png", content)).json()
        assert a == b

    async def test_unreadable_has_no_fields_and_is_not_an_error(self, admin):
        r = await extract(admin, "expense", "r.png", unreadable_png())
        assert r.status_code == 200
        assert r.json()["status"] == "unreadable"
        assert r.json()["fields"] == []

    async def test_saves_nothing(self, admin):
        before = (await admin.get(f"{API}/tickets/?limit=1")).json()["total"]
        await extract(admin, "expense", "r.png", readable_png())
        assert (await admin.get(f"{API}/tickets/?limit=1")).json()["total"] == before

    async def test_low_confidence_fields_require_review(self, admin):
        for _ in range(40):
            body = (await extract(admin, "expense", "r.png", readable_png(low=True))).json()
            lows = sorted(f["key"] for f in body["fields"] if f["confidence"] == "low")
            assert sorted(body["review_required"]) == lows
            if lows:
                return
        pytest.fail("no sample produced a low-confidence field")

    async def test_all_high_requires_no_review(self, admin):
        body = (await extract(admin, "expense", "r.png", readable_png(low=False))).json()
        assert body["review_required"] == []
        assert all(f["confidence"] == "high" for f in body["fields"])

    async def test_pdf_is_not_read(self, admin):
        r = await extract(admin, "expense", "r.pdf", pdf_bytes())
        assert r.status_code == 422

    async def test_type_without_amount_is_422(self, admin):
        r = await extract(admin, "hangar_incident", "r.png", readable_png())
        assert r.status_code == 422

    async def test_unknown_type_is_404(self, admin):
        r = await extract(admin, "nope", "r.png", readable_png())
        assert r.status_code == 404
        assert r.json()["detail"] == "El tipo de registro no existe."

    async def test_spoofed_image_is_refused(self, admin):
        r = await extract(admin, "expense", "r.png", pdf_bytes())
        assert r.status_code == 422
        assert r.json()["codigo"] == "tipo"

    async def test_bad_extension_is_400(self, admin):
        assert (await extract(admin, "expense", "r.exe", png_bytes())).status_code == 400

    async def test_empty_is_400(self, admin):
        assert (await extract(admin, "expense", "r.png", b"")).status_code == 400

    async def test_oversized_is_413(self, admin):
        big = b"\x89PNG\r\n\x1a\n" + b"0" * (23 * 1024 * 1024)
        assert (await extract(admin, "expense", "r.png", big)).status_code == 413

    async def test_anonymous_is_401(self, anon):
        assert (await extract(anon, "expense", "r.png", readable_png())).status_code == 401

    async def test_without_create_or_edit_permission_is_denied(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        r = await extract(api, "expense", "r.png", readable_png())
        assert r.status_code in (403, 404)

    async def test_a_type_the_user_cannot_use_is_404_like_a_missing_one(self, new_account, admin):
        """Resource-scoped by type: only the per-type edit permission opens `extract`."""
        api, _, _ = await new_account(["tickets.edit.hangar_incident", "tickets.view"])
        missing = await extract(api, "nope", "r.png", readable_png())
        foreign = await extract(api, "expense", "r.png", readable_png())
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()

    async def test_the_per_type_permission_does_open_its_own_type(self, new_account):
        api, _, _ = await new_account(["tickets.edit.expense", "tickets.view.expense"])
        assert (await extract(api, "expense", "r.png", readable_png())).status_code == 200

    async def test_missing_type_code_is_422(self, admin):
        r = await admin.post(
            f"{API}/attachments/extract/", files={"file": ("r.png", readable_png(), "image/png")}
        )
        assert r.status_code == 422

    async def test_pilot_may_read_for_expenses(self, pilot):
        assert (await extract(pilot, "expense", "r.png", readable_png())).status_code == 200


class TestReadingOnUpload:
    async def test_a_readable_image_creates_a_reading_with_the_file(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "r.png", content=readable_png())
        reading = ticket["files"][0]["extraction"]
        assert reading is not None
        assert reading["status"] == "extracted"
        assert reading["source"] == "reader"

    async def test_unreadable_is_recorded_and_never_blocks(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "r.png", unreadable_png())
        assert r.status_code == 201
        reading = r.json()["files"][0]["extraction"]
        assert reading is None or reading["status"] == "unreadable"

    async def test_a_pdf_gets_no_reading(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "r.pdf")
        assert ticket["files"][0]["extraction"] is None

    async def test_a_type_without_amount_gets_no_reading(self, admin):
        op = await make_operation(admin)
        ticket = await attach(admin, op, "r.png", field_key="manifest")
        assert ticket["files"][0]["extraction"] is None

    async def test_reading_does_not_overwrite_what_the_user_typed(self, admin):
        _, expense = await new_expense(admin, amount=123)
        ticket = await attach(admin, expense, "r.png", content=readable_png())
        assert ticket["payload"]["amount"] == 123


class TestValidationGate:
    async def _with_reading(self, api, low: bool):
        _, expense = await new_expense(api)
        ticket = await attach(api, expense, "r.png", content=readable_png(low=low))
        return expense, ticket["files"][0]

    async def test_pending_queue_lists_extracted_readings(self, admin):
        expense, f = await self._with_reading(admin, low=True)
        r = await admin.get(f"{API}/attachments/pending/?ticket_id={expense['id']}")
        assert r.status_code == 200, r.text
        assert [x["file"]["id"] for x in r.json()["data"]] == [f["id"]]

    async def test_pending_queue_empty_when_nothing_to_validate(self, admin):
        _, expense = await new_expense(admin)
        await attach(admin, expense, "r.pdf")
        r = await admin.get(f"{API}/attachments/pending/?ticket_id={expense['id']}")
        assert r.json()["data"] == []

    async def test_pending_requires_login(self, anon):
        assert (await anon.get(f"{API}/attachments/pending/")).status_code == 401

    async def test_validating_with_doubt_needs_confirmation(self, admin):
        expense, f = await self._with_reading(admin, low=True)
        reading = f["extraction"]
        fields = [{"key": x["key"], "value": x["value"]} for x in reading["fields"]]
        r = await admin.post(
            f"{API}/tickets/{expense['id']}/attachments/{f['id']}/validate/",
            json={"fields": fields, "confirmed": []},
        )
        assert r.status_code in (409, 422)

    async def test_validating_with_confirmation_clears_the_queue(self, admin):
        expense, f = await self._with_reading(admin, low=True)
        fields = [
            {"key": x["key"], "value": "USD" if x["key"] == "currency" else x["value"]}
            for x in f["extraction"]["fields"]
        ]
        r = await admin.post(
            f"{API}/tickets/{expense['id']}/attachments/{f['id']}/validate/",
            json={"fields": fields, "confirmed": [x["key"] for x in fields]},
        )
        assert r.status_code == 200, r.text
        pending = await admin.get(f"{API}/attachments/pending/?ticket_id={expense['id']}")
        assert pending.json()["data"] == []

    async def test_validate_unknown_file_is_404(self, admin):
        _, expense = await new_expense(admin)
        r = await admin.post(
            f"{API}/tickets/{expense['id']}/attachments/00000000-0000-0000-0000-000000000000/validate/",
            json={"fields": [], "confirmed": []},
        )
        assert r.status_code == 404

    async def test_validate_file_without_reading_is_404(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "r.pdf")
        r = await admin.post(
            f"{API}/tickets/{expense['id']}/attachments/{ticket['files'][0]['id']}/validate/",
            json={"fields": [], "confirmed": []},
        )
        assert r.status_code == 404

    async def test_removing_the_file_removes_its_pending_reading(self, admin):
        expense, f = await self._with_reading(admin, low=True)
        await admin.delete(f"{API}/tickets/{expense['id']}/attachments/{f['id']}/")
        pending = await admin.get(f"{API}/attachments/pending/?ticket_id={expense['id']}")
        assert pending.json()["data"] == []
        assert (await get_ticket(admin, expense))["attachments"] == 0

    async def test_pending_queue_is_scoped_to_what_the_caller_can_see(self, admin, new_account):
        expense, _ = await self._with_reading(admin, low=True)
        api, _, _ = await new_account(["notifications.view"])
        r = await api.get(f"{API}/attachments/pending/?ticket_id={expense['id']}")
        assert r.status_code == 403
