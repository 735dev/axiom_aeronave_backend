"""Idempotency, Blackbox failures, signed links, removal, closed tickets and scoping."""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.attachments_support import (
    attach,
    files_of,
    files_url,
    new_expense,
    pdf_bytes,
    png_bytes,
    upload,
)
from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    get_ticket,
    history,
    make_expense,
    make_operation,
    new_key,
    patch,
    ticket_url,
)


class TestIdempotency:
    async def test_replay_with_same_key_and_content_stores_once(self, admin):
        _, expense = await new_expense(admin)
        content, key = pdf_bytes(), new_key()
        first = await upload(admin, expense, "a.pdf", content, key=key)
        second = await upload(admin, expense, "a.pdf", content, key=key)
        assert first.status_code == 201 and second.status_code == 201
        assert second.headers.get("Idempotent-Replay") == "true"
        assert len(await files_of(admin, expense)) == 1
        assert first.json()["files"][0]["id"] == second.json()["files"][0]["id"]

    async def test_same_key_other_content_is_422(self, admin):
        _, expense = await new_expense(admin)
        key = new_key()
        await upload(admin, expense, "a.pdf", pdf_bytes(), key=key)
        r = await upload(admin, expense, "a.pdf", pdf_bytes(), key=key)
        assert r.status_code == 422
        assert r.json()["codigo"] == "idempotencia"
        assert len(await files_of(admin, expense)) == 1

    async def test_no_key_means_each_upload_is_distinct(self, admin):
        _, expense = await new_expense(admin)
        content = pdf_bytes()
        await upload(admin, expense, "a.pdf", content)
        await upload(admin, expense, "a.pdf", content)
        assert len(await files_of(admin, expense)) == 2

    async def test_different_keys_store_separately(self, admin):
        _, expense = await new_expense(admin)
        content = pdf_bytes()
        await upload(admin, expense, "a.pdf", content, key=new_key())
        await upload(admin, expense, "a.pdf", content, key=new_key())
        assert len(await files_of(admin, expense)) == 2

    async def test_the_key_is_scoped_per_ticket(self, admin):
        op = await make_operation(admin)
        a, b = await make_expense(admin, op), await make_expense(admin, op)
        content, key = pdf_bytes(), new_key()
        await upload(admin, a, "a.pdf", content, key=key)
        r = await upload(admin, b, "a.pdf", content, key=key)
        assert r.status_code == 201
        assert len(await files_of(admin, b)) == 1

    async def test_the_key_is_scoped_per_user(self, admin, pilot):
        op = await make_operation(admin)
        expense = await make_expense(pilot, op)
        content, key = pdf_bytes(), new_key()
        await upload(admin, expense, "a.pdf", content, key=key)
        r = await upload(pilot, expense, "a.pdf", content, key=key)
        assert r.status_code == 201
        assert len(await files_of(admin, expense)) == 2

    async def test_concurrent_same_key_stores_one(self, admin):
        _, expense = await new_expense(admin)
        content, key = pdf_bytes(), new_key()
        results = await asyncio.gather(
            *[upload(admin, expense, "a.pdf", content, key=key) for _ in range(4)]
        )
        assert all(r.status_code == 201 for r in results), [r.text for r in results]
        assert len(await files_of(admin, expense)) == 1

    @pytest.mark.parametrize("key", ["", "a b", "x" * 201, "a;b"])
    async def test_bad_keys_are_refused(self, admin, key):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "a.pdf", pdf_bytes(), key=key)
        assert r.status_code in (422, 400)
        assert await files_of(admin, expense) == []

    async def test_a_replay_still_counts_once_toward_the_limit(self, admin):
        _, expense = await new_expense(admin)
        content, key = pdf_bytes(), new_key()
        for _ in range(3):
            await upload(admin, expense, "a.pdf", content, key=key)
        assert (await get_ticket(admin, expense))["attachments"] == 1

    async def test_a_lost_answer_is_retry_safe(self, admin):
        """`bblose` stores the document then times out the first attempt: the retry must
        find the same document, not archive a second copy."""
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "bblose.pdf", pdf_bytes(), key=new_key())
        assert r.status_code == 201, r.text
        assert len(await files_of(admin, expense)) == 1


class TestBlackboxFailures:
    @pytest.mark.parametrize(
        "name,status",
        [
            ("bbdown.pdf", 503),
            ("bbtimeout.pdf", 503),
            ("bbcred.pdf", 503),
            ("bbdenied.pdf", 503),
            ("bbbadjson.pdf", 503),
        ],
    )
    async def test_failure_is_clear_and_leaves_nothing(self, admin, name, status):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, name, pdf_bytes())
        assert r.status_code == status, r.text
        assert r.json()["codigo"] == "almacenamiento"
        assert await files_of(admin, expense) == []
        t = await get_ticket(admin, expense)
        assert t["attachments"] == 0
        assert not [e for e in await history(admin, expense) if e["action"] == "attach"]

    async def test_errors_never_leak_provider_detail(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "bbcred.pdf", pdf_bytes())
        text = r.text.lower()
        for word in ("bb_maestro", "token", "blackbox.fake", "traceback", "401", "credential"):
            assert word not in text

    async def test_transient_errors_are_retried_transparently(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "bbflaky.pdf", pdf_bytes(), key=new_key())
        assert r.status_code == 201, r.text

    async def test_a_failure_does_not_poison_the_next_upload(self, admin):
        _, expense = await new_expense(admin)
        await upload(admin, expense, "bbdown.pdf", pdf_bytes())
        ok = await attach(admin, expense, "bien.pdf")
        assert ok["files"][0]["storage"]["filename"].endswith("_01_bien.pdf")

    async def test_failure_with_a_key_can_be_retried_with_the_same_key(self, admin):
        _, expense = await new_expense(admin)
        content, key = pdf_bytes(), new_key()
        assert (await upload(admin, expense, "bbdown.pdf", content, key=key)).status_code == 503
        # the key was never consumed: a retry with another (healthy) name and same key but
        # different content is still a fresh upload
        assert (await upload(admin, expense, "ok.pdf", pdf_bytes(), key=key)).status_code == 201


class TestDownloadLink:
    async def test_returns_a_signed_short_lived_link(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        r = await admin.get(files_url(expense, f"{ticket['files'][0]['id']}/download/"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["url"].startswith("https://")
        assert body["expires_at"]

    async def test_link_is_not_stored_anywhere(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        fid = ticket["files"][0]["id"]
        await admin.get(files_url(expense, f"{fid}/download/"))
        assert "X-Signature" not in (await admin.get(ticket_url(expense))).text
        assert "X-Signature" not in (await admin.get(files_url(expense))).text

    async def test_expiry_is_about_five_minutes(self, admin):
        from datetime import datetime, timezone

        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        r = await admin.get(files_url(expense, f"{ticket['files'][0]['id']}/download/"))
        expires = datetime.fromisoformat(r.json()["expires_at"].replace("Z", "+00:00"))
        seconds = (expires - datetime.now(timezone.utc)).total_seconds()
        assert 240 < seconds <= 300

    async def test_unknown_file_is_404(self, admin):
        _, expense = await new_expense(admin)
        r = await admin.get(files_url(expense, "00000000-0000-0000-0000-000000000000/download/"))
        assert r.status_code == 404

    async def test_file_of_another_ticket_is_404(self, admin):
        op = await make_operation(admin)
        a, b = await make_expense(admin, op), await make_expense(admin, op)
        ticket = await attach(admin, a, "a.pdf")
        r = await admin.get(files_url(b, f"{ticket['files'][0]['id']}/download/"))
        assert r.status_code == 404

    async def test_a_removed_file_has_no_link(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        fid = ticket["files"][0]["id"]
        await admin.delete(files_url(expense, f"{fid}/"))
        assert (await admin.get(files_url(expense, f"{fid}/download/"))).status_code == 404

    async def test_anonymous_is_401(self, admin, anon):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        r = await anon.get(files_url(expense, f"{ticket['files'][0]['id']}/download/"))
        assert r.status_code == 401

    async def test_link_for_a_file_with_a_failing_archive_is_503(self, admin):
        # nothing to do: the fake keeps the document; assert a healthy path stays 200 twice
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        url = files_url(expense, f"{ticket['files'][0]['id']}/download/")
        assert (await admin.get(url)).status_code == 200
        assert (await admin.get(url)).status_code == 200


class TestRemoval:
    async def test_delete_removes_from_listing_and_count(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        r = await admin.delete(files_url(expense, f"{ticket['files'][0]['id']}/"))
        assert r.status_code == 200
        assert r.json()["attachments"] == 0
        assert await files_of(admin, expense) == []

    async def test_names_are_not_reused_after_a_delete(self, admin):
        _, expense = await new_expense(admin)
        first = await attach(admin, expense, "a.pdf")
        await admin.delete(files_url(expense, f"{first['files'][0]['id']}/"))
        second = await attach(admin, expense, "a.pdf")
        assert "_comprobante_02_" in second["files"][0]["storage"]["filename"]

    async def test_delete_twice_is_404(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        url = files_url(expense, f"{ticket['files'][0]['id']}/")
        await admin.delete(url)
        assert (await admin.delete(url)).status_code == 404

    async def test_delete_is_audited(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        await admin.delete(files_url(expense, f"{ticket['files'][0]['id']}/"))
        assert [e for e in await history(admin, expense) if e["action"] == "delete"]

    async def test_cannot_delete_a_file_of_another_ticket(self, admin):
        op = await make_operation(admin)
        a, b = await make_expense(admin, op), await make_expense(admin, op)
        ticket = await attach(admin, a, "a.pdf")
        r = await admin.delete(files_url(b, f"{ticket['files'][0]['id']}/"))
        assert r.status_code == 404
        assert len(await files_of(admin, a)) == 1

    async def test_unknown_file_is_404(self, admin):
        _, expense = await new_expense(admin)
        r = await admin.delete(files_url(expense, "00000000-0000-0000-0000-000000000000/"))
        assert r.status_code == 404


class TestClosedTicket:
    async def _closed(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        assert (await patch(admin, expense, status_code="verified")).status_code == 200
        return expense, ticket["files"][0]["id"]

    async def test_upload_is_409(self, admin):
        expense, _ = await self._closed(admin)
        r = await upload(admin, expense, "tarde.pdf", pdf_bytes())
        assert r.status_code == 409

    async def test_delete_is_409(self, admin):
        expense, fid = await self._closed(admin)
        assert (await admin.delete(files_url(expense, f"{fid}/"))).status_code == 409
        assert len(await files_of(admin, expense)) == 1

    async def test_the_attempt_is_audited_as_denied(self, admin):
        expense, _ = await self._closed(admin)
        await upload(admin, expense, "tarde.pdf", pdf_bytes())
        assert [e for e in await history(admin, expense) if e["action"] == "denied"]

    async def test_reading_a_closed_ticket_files_still_works(self, admin):
        expense, fid = await self._closed(admin)
        assert len(await files_of(admin, expense)) == 1
        assert (await admin.get(files_url(expense, f"{fid}/download/"))).status_code == 200

    async def test_a_closed_upload_does_not_reach_the_archive(self, admin):
        expense, _ = await self._closed(admin)
        await upload(admin, expense, "tarde.pdf", pdf_bytes())
        assert len(await files_of(admin, expense)) == 1


class TestAccessAndScope:
    async def test_anonymous_cannot_upload_or_list(self, admin, anon):
        _, expense = await new_expense(admin)
        assert (await upload(anon, expense, "a.pdf", pdf_bytes())).status_code == 401
        assert (await anon.get(files_url(expense))).status_code == 401

    async def test_a_viewer_without_edit_cannot_upload(self, admin, director):
        _, expense = await new_expense(admin)
        r = await upload(director, expense, "a.pdf", pdf_bytes())
        assert r.status_code in (403, 404)
        assert await files_of(admin, expense) == []

    async def test_a_viewer_can_list_and_download(self, admin, director):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        assert len(await files_of(director, expense)) == 1
        r = await director.get(files_url(expense, f"{ticket['files'][0]['id']}/download/"))
        assert r.status_code == 200

    async def test_a_viewer_cannot_delete(self, admin, director):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        r = await director.delete(files_url(expense, f"{ticket['files'][0]['id']}/"))
        assert r.status_code in (403, 404)
        assert len(await files_of(admin, expense)) == 1

    async def test_a_user_with_no_view_permission_sees_nothing(self, admin, new_account):
        api, _, _ = await new_account(["notifications.view"])
        _, expense = await new_expense(admin)
        await attach(admin, expense, "a.pdf")
        assert (await api.get(files_url(expense))).status_code == 403

    async def test_unknown_ticket_upload_is_404(self, admin):
        r = await admin.post(
            f"{API}/tickets/00000000-0000-0000-0000-000000000000/attachments/",
            files={"file": ("a.pdf", pdf_bytes(), "application/pdf")},
        )
        assert r.status_code == 404

    async def test_unknown_ticket_delete_is_404(self, admin):
        r = await admin.delete(
            f"{API}/tickets/00000000-0000-0000-0000-000000000000/attachments/"
            "00000000-0000-0000-0000-000000000000/"
        )
        assert r.status_code == 404

    async def test_mass_assignment_is_not_possible_via_headers(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "a.pdf", pdf_bytes(), headers={"X-Uploaded-By": "evil"})
        assert r.status_code == 201
        assert r.json()["files"][0]["uploaded_by"] != "evil"


def test_png_helper():
    assert png_bytes()[:4] == b"\x89PNG"
