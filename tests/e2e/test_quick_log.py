"""Quick log (bitácora rápida): the technical report created or completed from the flight."""

import uuid

from tests.e2e.attachments_support import attach, files_of
from tests.e2e.closing_support import closed_operation, resolve_reports
from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    children,
    get_ticket,
    history,
    make_operation,
    patch,
    ticket_url,
)

TIMES = {"actual_departure": "08:00", "actual_arrival": "09:30"}
NO_FINDING = "Sin novedad en el vuelo"


def log_body(op: dict, **payload) -> dict:
    return {
        "type_code": "technical_report",
        "title": f"Reporte técnico · {op['code']}",
        "parent_id": op["id"],
        "payload": {**TIMES, "has_finding": False, "description": NO_FINDING, **payload},
    }


async def create(api, op, key=None, **payload):
    headers = {"Idempotency-Key": key} if key else {}
    return await api.post(f"{API}/tickets/", json=log_body(op, **payload), headers=headers)


async def open_reports(api, op) -> list[dict]:
    return await children(api, op, type_code="technical_report", open="true")


def key() -> str:
    return f"ql-{uuid.uuid4().hex}"


class TestCreate:
    async def test_creates_a_technical_report_child_with_the_payload(self, pilot, admin):
        op = await make_operation(admin)
        r = await create(pilot, op, has_finding=True, description="Ruido en el motor")
        assert r.status_code == 201, r.text
        t = r.json()
        assert t["type_code"] == "technical_report" and t["parent_id"] == op["id"]
        assert t["parent_code"] == op["code"]
        assert t["payload"] == {
            **TIMES,
            "has_finding": True,
            "description": "Ruido en el motor",
        }

    async def test_without_a_finding_the_description_is_explicit(self, pilot, admin):
        op = await make_operation(admin)
        t = (await create(pilot, op)).json()
        assert t["payload"]["has_finding"] is False and t["payload"]["description"] == NO_FINDING

    async def test_the_report_is_assigned_to_the_maintenance_role_by_default(self, pilot, admin):
        op = await make_operation(admin)
        t = (await create(pilot, op)).json()
        assert t["assigned_role_id"]

    async def test_the_night_flag_is_stored_only_when_it_applies(self, pilot, admin):
        op = await make_operation(admin)
        plain = (await create(pilot, op)).json()
        night = (
            await create(
                pilot, op, actual_departure="22:00", actual_arrival="01:10", arrival_next_day=True
            )
        ).json()
        assert "arrival_next_day" not in plain["payload"]
        assert night["payload"]["arrival_next_day"] is True

    async def test_the_photo_is_attached_to_the_new_report(self, pilot, admin):
        op = await make_operation(admin)
        t = (await create(pilot, op, key(), has_finding=True, description="Ala")).json()
        await attach(pilot, t, "ala.png", field_key="photo")
        assert len(await files_of(pilot, t)) == 1

    async def test_the_operation_counts_its_new_child(self, pilot, admin):
        op = await make_operation(admin)
        before = (await get_ticket(admin, op))["children_count"]
        await create(pilot, op)
        assert (await get_ticket(admin, op))["children_count"] == before + 1

    async def test_the_creation_is_audited(self, pilot, admin):
        op = await make_operation(admin)
        t = (await create(pilot, op)).json()
        assert any(e["action"] == "create" for e in await history(admin, t))

    async def test_a_closed_operation_refuses_the_log_with_409(self, ops, pilot):
        op = await closed_operation(ops)
        assert (await create(pilot, op, key())).status_code == 409

    async def test_an_unknown_operation_is_404(self, pilot):
        body = log_body({"id": "00000000-0000-0000-0000-000000000000", "code": "X"})
        assert (await pilot.post(f"{API}/tickets/", json=body)).status_code in (404, 422)

    async def test_anonymous_is_401(self, anon, admin):
        op = await make_operation(admin)
        assert (await create(anon, op)).status_code == 401


class TestIdempotentResend:
    async def test_the_same_key_creates_a_single_report(self, pilot, admin):
        op = await make_operation(admin)
        before = len(await children(admin, op, type_code="technical_report"))
        k = key()
        a, b = await create(pilot, op, k), await create(pilot, op, k)
        assert a.status_code == b.status_code == 201
        assert a.json()["id"] == b.json()["id"]
        assert b.headers.get("idempotent-replay") == "true"
        assert len(await children(admin, op, type_code="technical_report")) == before + 1

    async def test_another_key_creates_another_report(self, pilot, admin):
        op = await make_operation(admin)
        a, b = await create(pilot, op, key()), await create(pilot, op, key())
        assert a.json()["id"] != b.json()["id"]

    async def test_the_same_key_with_a_different_body_is_refused(self, pilot, admin):
        op = await make_operation(admin)
        k = key()
        await create(pilot, op, k)
        r = await create(pilot, op, k, description="Otra cosa distinta")
        assert r.status_code in (409, 422)

    async def test_a_photo_resend_with_the_same_key_stores_one_file(self, pilot, admin):
        op = await make_operation(admin)
        t = (await create(pilot, op, key())).json()
        content = b"\x89PNG\r\n\x1a\n" + uuid.uuid4().bytes * 4
        k = f"{key()}:a0"
        for _ in range(2):
            r = await pilot.post(
                ticket_url(t, "attachments/"),
                files={"file": ("ala.png", content, "image/png")},
                data={"field_key": "photo"},
                headers={"Idempotency-Key": k},
            )
            assert r.status_code == 201, r.text
        assert len(await files_of(pilot, t)) == 1

    async def test_a_key_is_per_user(self, pilot, admin):
        op = await make_operation(admin)
        k = key()
        a = await create(pilot, op, k)
        b = await create(admin, op, k)
        assert a.json()["id"] != b.json()["id"]


class TestTimes:
    async def test_a_badly_formed_time_is_422_naming_the_field(self, pilot, admin):
        op = await make_operation(admin)
        for bad in ("25:00", "9:5", "0830", "ab:cd", "08:60"):
            r = await create(pilot, op, actual_departure=bad)
            assert r.status_code == 422, bad
            assert "actual_departure" in r.json()["errors"]

    async def test_arrival_before_departure_is_422_without_the_night_flag(self, pilot, admin):
        op = await make_operation(admin)
        r = await create(pilot, op, actual_departure="22:00", actual_arrival="01:10")
        assert r.status_code == 422 and "actual_arrival" in r.json()["errors"]
        assert "día siguiente" in r.json()["errors"]["actual_arrival"]

    async def test_arrival_before_departure_is_valid_after_midnight(self, pilot, admin):
        op = await make_operation(admin)
        r = await create(
            pilot, op, actual_departure="22:00", actual_arrival="01:10", arrival_next_day=True
        )
        assert r.status_code == 201

    async def test_equal_times_are_refused(self, pilot, admin):
        op = await make_operation(admin)
        r = await create(pilot, op, actual_departure="08:00", actual_arrival="08:00")
        assert r.status_code == 422 and "misma hora" in r.json()["errors"]["actual_arrival"]

    async def test_a_refused_log_creates_nothing(self, pilot, admin):
        op = await make_operation(admin)
        before = len(await children(admin, op))
        await create(pilot, op, actual_departure="22:00", actual_arrival="01:10")
        assert len(await children(admin, op)) == before

    async def test_the_edge_times_are_accepted(self, pilot, admin):
        op = await make_operation(admin)
        r = await create(pilot, op, actual_departure="00:00", actual_arrival="23:59")
        assert r.status_code == 201

    async def test_a_patch_is_checked_against_the_stored_other_time(self, pilot, admin):
        op = await make_operation(admin)
        t = (await create(pilot, op)).json()
        r = await patch(pilot, t, payload={"actual_arrival": "07:00"})
        assert r.status_code == 422
        assert (await get_ticket(pilot, t))["payload"]["actual_arrival"] == "09:30"

    async def test_a_patch_moving_both_times_is_judged_together(self, pilot, admin):
        op = await make_operation(admin)
        t = (await create(pilot, op)).json()
        r = await patch(pilot, t, payload={"actual_departure": "20:00", "actual_arrival": "21:15"})
        assert r.status_code == 200


class TestCompleteTheOpenReport:
    async def _pending(self, admin, pilot):
        op = await make_operation(admin)
        [report] = await open_reports(pilot, op)
        return op, report

    async def test_the_operation_is_born_with_an_open_report(self, admin, pilot):
        _, report = await self._pending(admin, pilot)
        assert report["status_code"] == "reported" and report["status_is_final"] is False

    async def test_a_partial_payload_merges_into_the_existing_one(self, admin, pilot):
        _, report = await self._pending(admin, pilot)
        r = await patch(
            pilot, report, payload={**TIMES, "has_finding": True, "description": "Fuga"}
        )
        assert r.status_code == 200
        assert r.json()["payload"] == {**TIMES, "has_finding": True, "description": "Fuga"}

    async def test_untouched_keys_survive_a_partial_patch(self, admin, pilot):
        _, report = await self._pending(admin, pilot)
        await patch(pilot, report, payload=TIMES)
        r = await patch(pilot, report, payload={"has_finding": True})
        assert r.json()["payload"]["description"] == report["payload"]["description"]
        assert r.json()["payload"]["actual_departure"] == "08:00"

    async def test_the_patch_keeps_the_child_and_creates_no_other(self, admin, pilot):
        op, report = await self._pending(admin, pilot)
        before = len(await children(admin, op))
        r = await patch(
            pilot, report, payload={**TIMES, "has_finding": True, "description": "Ruido"}
        )
        assert r.json()["id"] == report["id"]
        assert len(await children(admin, op)) == before

    async def test_the_photo_goes_to_the_patched_report(self, admin, pilot):
        _, report = await self._pending(admin, pilot)
        await patch(pilot, report, payload=TIMES)
        await attach(pilot, report, "ala.png", field_key="photo")
        assert len(await files_of(pilot, report)) == 1

    async def test_the_payload_patch_is_replayable_with_the_same_key(self, admin, pilot):
        _, report = await self._pending(admin, pilot)
        k = key()
        body = {"payload": {**TIMES, "has_finding": True, "description": "Ruido"}}
        a = await pilot.patch(ticket_url(report), json=body, headers={"Idempotency-Key": k})
        b = await pilot.patch(ticket_url(report), json=body, headers={"Idempotency-Key": k})
        assert a.status_code == b.status_code == 200
        assert a.json()["payload"] == b.json()["payload"]

    async def test_an_unknown_payload_key_is_422(self, admin, pilot):
        _, report = await self._pending(admin, pilot)
        r = await patch(pilot, report, payload={"hacked": 1})
        assert r.status_code == 422 and "hacked" in r.json()["errors"]

    async def test_a_non_boolean_finding_flag_is_422(self, admin, pilot):
        _, report = await self._pending(admin, pilot)
        assert (await patch(pilot, report, payload={"has_finding": "maybe"})).status_code == 422

    async def test_the_oldest_open_report_is_the_one_to_complete(self, admin, pilot):
        op = await make_operation(admin)
        [first] = await open_reports(pilot, op)
        await create(pilot, op, key())
        rows = sorted(await open_reports(pilot, op), key=lambda t: t["created_at"])
        assert rows[0]["id"] == first["id"]

    async def test_a_resolved_report_is_not_in_the_open_list(self, admin, pilot):
        op = await make_operation(admin)
        await resolve_reports(admin, op)
        assert await open_reports(pilot, op) == []

    async def test_a_viewer_without_edit_permission_cannot_patch(self, new_account, admin):
        viewer, _, _ = await new_account(["tickets.view"])
        op = await make_operation(admin)
        [report] = await children(admin, op, type_code="technical_report")
        assert (await patch(viewer, report, payload=TIMES)).status_code == 403

    async def test_a_report_editor_may_complete_it(self, new_account, admin):
        editor, _, _ = await new_account(["tickets.view", "tickets.edit.technical_report"])
        op = await make_operation(admin)
        [report] = await children(admin, op, type_code="technical_report")
        assert (await patch(editor, report, payload=TIMES)).status_code == 200

    async def test_a_report_of_a_closed_operation_is_locked(self, ops, pilot):
        op = await closed_operation(ops)
        [report] = await children(ops, op, type_code="technical_report")
        assert (await patch(pilot, report, payload=TIMES)).status_code == 409


class TestNoFindings:
    async def test_no_findings_resolves_the_report(self, admin, pilot):
        op = await make_operation(admin)
        [report] = await open_reports(pilot, op)
        r = await patch(
            pilot, report, payload={**TIMES, "has_finding": False}, status_code="no_findings"
        )
        assert r.status_code == 200
        assert r.json()["status_is_final"] is True and r.json()["status_code"] == "no_findings"
        assert r.json()["payload"]["actual_arrival"] == "09:30"

    async def test_it_does_not_count_as_committed(self, admin):
        op = await make_operation(admin)
        [report] = await children(admin, op, type_code="technical_report")
        r = await patch(admin, report, status_code="no_findings")
        assert r.json()["status_counts_as_committed"] is False

    async def test_a_resolved_report_is_immutable(self, admin):
        op = await make_operation(admin)
        [report] = await children(admin, op, type_code="technical_report")
        await patch(admin, report, status_code="no_findings")
        assert (await patch(admin, report, payload=TIMES)).status_code == 409
        assert (await patch(admin, report, status_code="reported")).status_code == 409

    async def test_it_unblocks_the_operation_close_checklist(self, admin):
        from tests.e2e.closing_support import closing

        op = await make_operation(admin)
        await resolve_reports(admin, op)
        blockers = (await closing(admin, op))["blockers"]
        assert not [b for b in blockers if b["kind"] == "unresolved_child"]

    async def test_the_report_with_a_finding_is_not_resolved_by_just_flying(self, admin):
        op = await make_operation(admin)
        [report] = await children(admin, op, type_code="technical_report")
        await patch(admin, report, payload={"has_finding": True, "description": "Fuga"})
        assert (await get_ticket(admin, report))["status_is_final"] is False

    async def test_the_status_change_is_in_the_history(self, admin):
        op = await make_operation(admin)
        [report] = await children(admin, op, type_code="technical_report")
        await patch(admin, report, status_code="no_findings")
        assert any(e["action"] == "status_change" for e in await history(admin, report))
