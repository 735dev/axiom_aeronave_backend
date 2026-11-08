"""Retention of passengers' personal data: closed operations past the line are anonymized."""

import uuid

from tests.e2e.attachments_support import attach, files_of
from tests.e2e.closing_support import close_operation, ready_operation, set_signature, sign
from tests.e2e.conftest import API
from tests.e2e.live_support import run_cli
from tests.e2e.test_passengers import put
from tests.e2e.tickets_support import ticket_url

SCRIPT = "app.scripts.purge_personal_data"
LINE = {"PASSENGER_RETENTION_DAYS": "2000"}  # only the 2020 operations are past it


def person(tag: str, n: int, passport: str | None = None) -> dict:
    data = {
        "full_name": f"Pasajera {tag} {n}",
        "document": f"V{abs(hash((tag, n))) % 10**8:08d}",
        "nationality": "VE",
    }
    if passport:
        data["passport_file_id"] = passport
    return data


async def manifest(api, op) -> list[dict]:
    r = await api.get(ticket_url(op, "passengers/"))
    assert r.status_code == 200, r.text
    return r.json()


async def operation_with_manifest(api, tag: str, day: str, close: bool) -> tuple[dict, list[dict]]:
    await set_signature(api)
    op = await ready_operation(api, date=day)
    passport = (await attach(api, op, "pasaporte.png", field_key="passport"))["files"][0]["id"]
    people = [person(tag, 1, passport), person(tag, 2)]
    assert (await put(api, op, people)).status_code == 200
    if close:
        assert (await sign(api, op)).status_code == 201
        assert (await close_operation(api, op)).status_code == 200
    return op, people


async def search(api, tag: str) -> list[dict]:
    r = await api.get(f"{API}/passengers/search/", params={"q": f"Pasajera {tag}"})
    assert r.status_code == 200, r.text
    return r.json()["data"]


class TestAnonymization:
    async def test_closed_operations_past_the_line_lose_their_personal_data(self, admin):
        tag = uuid.uuid4().hex[:8]
        old, people = await operation_with_manifest(admin, tag, "2020-03-10", close=True)
        before = await manifest(admin, old)
        assert [p["full_name"] for p in before] == [p["full_name"] for p in people]
        assert before[0]["passport_file_id"] and await search(admin, tag)

        done = run_cli(SCRIPT, **LINE)
        assert done.returncode == 0, done.stderr
        assert "passengers anonymized: " in done.stdout

        after = await manifest(admin, old)
        assert [p["id"] for p in after] == [p["id"] for p in before]  # same rows, same order
        for p in after:
            assert p["full_name"] == "Pasajero anonimizado"
            assert p["document"].startswith("ANON-")
            assert p["nationality"] is None and p["passport_file_id"] is None
        assert len({p["document"] for p in after}) == len(after)
        assert await search(admin, tag) == []  # the old name no longer finds anything
        assert not [f for f in await files_of(admin, old) if f["name"] == "pasaporte.png"]

    async def test_recent_and_open_operations_are_untouched(self, admin):
        tag = uuid.uuid4().hex[:8]
        recent, _ = await operation_with_manifest(admin, tag, "2026-08-19", close=True)
        open_old, _ = await operation_with_manifest(admin, tag, "2020-04-01", close=False)
        run_cli(SCRIPT, **LINE)
        for op in (recent, open_old):
            rows = await manifest(admin, op)
            assert rows[0]["full_name"].startswith("Pasajera ") and rows[0]["nationality"] == "VE"
            assert rows[0]["passport_file_id"]
        assert len(await search(admin, tag)) == 2

    async def test_the_run_is_idempotent_and_leaves_one_audit_event_without_names(self, admin):
        tag = uuid.uuid4().hex[:8]
        op, _ = await operation_with_manifest(admin, tag, "2020-05-05", close=True)
        run_cli(SCRIPT, **LINE)
        first = (await admin.get(f"{API}/audit/?entity=passenger&limit=100")).json()["data"]
        assert first and first[0]["summary"].startswith("Anonimizó ")
        assert tag not in str(first)
        again = run_cli(SCRIPT, **LINE)
        assert "passengers anonymized: 0" in again.stdout
        assert len(
            (await admin.get(f"{API}/audit/?entity=passenger&limit=100")).json()["data"]
        ) == len(first)

    async def test_dry_run_changes_nothing_and_zero_disables_the_policy(self, admin):
        tag = uuid.uuid4().hex[:8]
        op, people = await operation_with_manifest(admin, tag, "2020-06-06", close=True)
        dry = run_cli(SCRIPT, "--dry-run", **LINE)
        off = run_cli(SCRIPT, PASSENGER_RETENTION_DAYS="0")
        assert dry.returncode == 0 and off.returncode == 0
        rows = await manifest(admin, op)
        assert [r["full_name"] for r in rows] == [p["full_name"] for p in people]

    async def test_a_manifest_can_still_be_replaced_but_anonymized_rows_do_not_come_back(
        self, admin
    ):
        tag = uuid.uuid4().hex[:8]
        op, _ = await operation_with_manifest(admin, tag, "2020-07-07", close=True)
        run_cli(SCRIPT, **LINE)
        r = await put(admin, op, [person(tag, 9)])
        assert r.status_code == 409
