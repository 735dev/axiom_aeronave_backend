"""The call to Blackbox runs without the ticket row locked, and survives crashes between steps.

`bbslow` (see the Blackbox fake) stores the document and then takes ~2 seconds to answer, so
the upload sits in the external call long enough for other requests to touch the same ticket.
"""

import asyncio
import time

from tests.e2e.attachments_support import files_of, new_expense, pdf_bytes, upload
from tests.e2e.tickets_support import get_ticket, history, new_key, patch

SLOW = 2.0  # the fake's delay
FAST = 1.0  # what a request that is not blocked takes, with margin


def names(files: list[dict]) -> list[str]:
    return sorted(f["storage"]["filename"] for f in files)


async def start_slow_upload(api, expense, name="bbslow.pdf", **kw):
    task = asyncio.create_task(upload(api, expense, name, pdf_bytes(), **kw))
    await asyncio.sleep(0.6)  # inside the external call by now
    return task


class TestNoLockDuringTheExternalCall:
    async def test_another_writer_on_the_ticket_is_not_blocked(self, admin):
        _, expense = await new_expense(admin)
        slow = await start_slow_upload(admin, expense)
        began = time.monotonic()
        r = await patch(admin, expense, title="editado mientras se archiva")
        elapsed = time.monotonic() - began
        assert r.status_code == 200, r.text
        assert elapsed < FAST, f"the edit waited {elapsed:.2f}s: the lock was held"
        done = await slow
        assert done.status_code == 201, done.text
        assert (await get_ticket(admin, expense))["title"] == "editado mientras se archiva"

    async def test_a_second_upload_overtakes_the_slow_one(self, admin):
        _, expense = await new_expense(admin)
        slow = await start_slow_upload(admin, expense, "bbslow.pdf")
        began = time.monotonic()
        quick = await upload(admin, expense, "rapido.pdf", pdf_bytes())
        assert quick.status_code == 201, quick.text
        assert time.monotonic() - began < FAST
        assert (await slow).status_code == 201
        assert len(await files_of(admin, expense)) == 2

    async def test_concurrent_uploads_never_share_an_ordinal(self, admin):
        _, expense = await new_expense(admin)
        results = await asyncio.gather(
            *(upload(admin, expense, f"bbslow{i}.pdf", pdf_bytes()) for i in range(3))
        )
        assert [r.status_code for r in results] == [201, 201, 201]
        ordinals = sorted(
            f["storage"]["filename"].split("_")[-2] for f in await files_of(admin, expense)
        )
        assert ordinals == ["01", "02", "03"]


class TestCrashBetweenTheSteps:
    async def test_dropped_after_archiving_then_retry_links_exactly_once(self, admin):
        """The client vanishes while Blackbox holds the file: nothing is linked, nothing is
        half written, and the same key finishes the job."""
        _, expense = await new_expense(admin)
        content, key = pdf_bytes(), new_key()
        first = asyncio.create_task(upload(admin, expense, "bbslow.pdf", content, key=key))
        await asyncio.sleep(0.6)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)

        assert await files_of(admin, expense) == []
        assert (await get_ticket(admin, expense))["attachments"] == 0
        assert not [e for e in await history(admin, expense) if e["action"] == "attach"]

        retry = await upload(admin, expense, "bbslow.pdf", content, key=key)
        assert retry.status_code == 201, retry.text
        files = await files_of(admin, expense)
        assert len(files) == 1 and files[0]["storage"]["filename"].endswith("_01_bbslow.pdf")
        again = await upload(admin, expense, "bbslow.pdf", content, key=key)
        assert again.headers.get("idempotent-replay") == "true"
        assert len(await files_of(admin, expense)) == 1

    async def test_the_orphan_slot_keeps_its_ordinal_reserved(self, admin):
        _, expense = await new_expense(admin)
        content, key = pdf_bytes(), new_key()
        crashed = asyncio.create_task(upload(admin, expense, "bbslow.pdf", content, key=key))
        await asyncio.sleep(0.6)
        crashed.cancel()
        await asyncio.gather(crashed, return_exceptions=True)

        other = await upload(admin, expense, "otro.pdf", pdf_bytes(), key=new_key())
        assert other.status_code == 201
        assert other.json()["files"][0]["storage"]["filename"].endswith("_02_otro.pdf")
        resumed = await upload(admin, expense, "bbslow.pdf", content, key=key)
        assert resumed.status_code == 201
        assert names(await files_of(admin, expense))[0].endswith("_01_bbslow.pdf")

    async def test_a_crash_does_not_use_up_the_file_limit_for_ever(self, admin):
        """Open slots count toward the limit while they live; a finished retry frees them."""
        _, expense = await new_expense(admin)
        content, key = pdf_bytes(), new_key()
        crashed = asyncio.create_task(upload(admin, expense, "bbslow.pdf", content, key=key))
        await asyncio.sleep(0.6)
        crashed.cancel()
        await asyncio.gather(crashed, return_exceptions=True)
        assert (await upload(admin, expense, "bbslow.pdf", content, key=key)).status_code == 201
        for i in range(9):
            assert (await upload(admin, expense, f"f{i}.pdf", pdf_bytes())).status_code == 201
        assert (await upload(admin, expense, "once.pdf", pdf_bytes())).status_code == 422
        assert len(await files_of(admin, expense)) == 10

    async def test_twin_requests_with_one_key_link_one_file(self, admin):
        _, expense = await new_expense(admin)
        content, key = pdf_bytes(), new_key()
        a, b = await asyncio.gather(
            upload(admin, expense, "bbslow.pdf", content, key=key),
            upload(admin, expense, "bbslow.pdf", content, key=key),
        )
        assert sorted([a.status_code, b.status_code]) == [201, 201]
        assert len(await files_of(admin, expense)) == 1
        assert [a.headers.get("idempotent-replay"), b.headers.get("idempotent-replay")].count(
            "true"
        ) == 1

    async def test_closed_while_archiving_is_refused_and_leaves_nothing(self, admin):
        _, expense = await new_expense(admin)
        slow = await start_slow_upload(admin, expense)
        assert (await patch(admin, expense, status_code="rejected")).status_code == 200
        done = await slow
        assert done.status_code == 409, done.text
        assert await files_of(admin, expense) == []
        assert (await get_ticket(admin, expense))["attachments"] == 0


class TestFailuresLeaveNothingHalfWritten:
    async def test_a_refused_archive_frees_the_ordinal_and_the_slot(self, admin):
        _, expense = await new_expense(admin)
        for _ in range(3):
            assert (await upload(admin, expense, "bbdown.pdf", pdf_bytes())).status_code == 503
        ok = await upload(admin, expense, "bien.pdf", pdf_bytes())
        assert ok.status_code == 201 and ok.json()["files"][0]["storage"]["filename"].endswith(
            "_01_bien.pdf"
        )

    async def test_failures_do_not_eat_the_file_limit(self, admin):
        _, expense = await new_expense(admin)
        for _ in range(12):
            await upload(admin, expense, "bbdown.pdf", pdf_bytes())
        for i in range(10):
            assert (await upload(admin, expense, f"g{i}.pdf", pdf_bytes())).status_code == 201
