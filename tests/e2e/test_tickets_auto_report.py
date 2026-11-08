from tests.e2e.conftest import API, png_data_url
from tests.e2e.tickets_support import (
    children,
    get_ticket,
    history,
    make_operation,
    patch,
    ticket_url,
)

STEPS = ["planning", "requirements", "ground", "technical", "signature"]


async def report_of(api, op):
    reports = [t for t in await children(api, op) if t["type_code"] == "technical_report"]
    assert len(reports) == 1
    return reports[0]


async def decide_all(api, op):
    # The closing needs the signer's own signature image: load one first.
    sig = await api.patch(f"{API}/auth/me/signature/", json={"signature_url": png_data_url()})
    assert sig.status_code == 200, sig.text
    for step in STEPS:
        r = await api.post(
            ticket_url(op, "decisions/"), json={"step_code": step, "decision": "done"}
        )
        assert r.status_code == 201, r.text


class TestAutomaticTechnicalReport:
    async def test_a_new_operation_carries_one_open_report(self, admin):
        op = await make_operation(admin)
        report = await report_of(admin, op)
        assert report["status_is_final"] is False and report["status_code"] == "reported"

    async def test_report_is_assigned_to_the_pilot_role(self, admin):
        report = await report_of(admin, await make_operation(admin))
        assert report["assigned_role_name"] == "Piloto"

    async def test_report_defaults_say_no_finding(self, admin):
        report = await report_of(admin, await make_operation(admin))
        assert report["payload"]["has_finding"] is False

    async def test_report_title_names_the_operation(self, admin):
        op = await make_operation(admin)
        assert op["code"] in (await report_of(admin, op))["title"]

    async def test_report_inherits_the_aircraft(self, admin):
        op = await make_operation(admin, aircraft="YV2528")
        report = await report_of(admin, op)
        assert report["aircraft_code"] == "YV2528"

    async def test_report_belongs_to_its_operation(self, admin):
        op = await make_operation(admin)
        report = await report_of(admin, op)
        assert report["parent_id"] == op["id"] and report["parent_code"] == op["code"]

    async def test_two_operations_do_not_share_reports(self, admin):
        a, b = await make_operation(admin), await make_operation(admin)
        ids_a = {t["id"] for t in await children(admin, a)}
        ids_b = {t["id"] for t in await children(admin, b)}
        assert ids_a and ids_b and not ids_a & ids_b

    async def test_a_failed_operation_leaves_no_orphan_report(self, admin):
        before = (await admin.get("/api/v1/tickets/?type_code=technical_report&limit=1")).json()
        await admin.post("/api/v1/tickets/", json={"type_code": "operation", "payload": {}})
        after = (await admin.get("/api/v1/tickets/?type_code=technical_report&limit=1")).json()
        assert after["total"] == before["total"]

    async def test_it_is_not_created_for_other_types(self, admin):
        r = await admin.post(
            "/api/v1/tickets/",
            json={"type_code": "hangar_incident", "payload": {"description": "golpe"}},
        )
        assert await children(admin, r.json()) == []

    async def test_report_creation_is_audited(self, admin):
        op = await make_operation(admin)
        report = await report_of(admin, op)
        events = await history(admin, report)
        assert any(e["action"] == "create" for e in events)

    async def test_pilot_can_work_the_report(self, pilot, admin):
        report = await report_of(admin, await make_operation(admin))
        r = await patch(pilot, report, payload={"has_finding": True, "description": "Fuga"})
        assert r.status_code == 200 and r.json()["payload"]["description"] == "Fuga"

    async def test_it_blocks_closing_the_operation(self, admin):
        op = await make_operation(admin)
        await decide_all(admin, op)
        r = await patch(admin, op, status_code="closed")
        assert r.status_code == 422
        assert (await report_of(admin, op))["code"] in r.json()["detail"]

    async def test_cancelling_does_not_require_resolving_it(self, admin):
        op = await make_operation(admin)
        assert (await patch(admin, op, status_code="cancelled")).status_code == 200

    async def test_no_findings_resolves_it_and_lets_the_operation_close(self, admin):
        op = await make_operation(admin)
        report = await report_of(admin, op)
        r = await patch(admin, report, status_code="no_findings")
        assert r.status_code == 200 and r.json()["status_is_final"] is True
        await decide_all(admin, op)
        closed = await patch(admin, op, status_code="closed")
        assert closed.status_code == 200, closed.text
        assert closed.json()["closed_at"] is not None

    async def test_a_resolved_report_is_locked(self, admin):
        report = await report_of(admin, await make_operation(admin))
        await patch(admin, report, status_code="no_findings")
        assert (await patch(admin, report, title="tarde")).status_code == 409

    async def test_missing_steps_still_block_the_close(self, admin):
        op = await make_operation(admin)
        await patch(admin, await report_of(admin, op), status_code="no_findings")
        r = await patch(admin, op, status_code="closed")
        assert r.status_code == 422 and "pasos" in r.json()["detail"].lower()

    async def test_the_report_that_found_something_can_be_solved_too(self, admin):
        op = await make_operation(admin)
        report = await report_of(admin, op)
        await patch(admin, report, status_code="in_review")
        refused = await patch(admin, report, status_code="solved")
        assert refused.status_code == 422 and "pasos" in refused.json()["detail"].lower()
        for step in (
            "report",
            "diagnosis",
            "movement",
            "commercial",
            "return",
            "verification",
            "airworthiness",
        ):
            r = await admin.post(
                ticket_url(report, "decisions/"), json={"step_code": step, "decision": "done"}
            )
            assert r.status_code == 201, r.text
        assert (await patch(admin, report, status_code="solved")).status_code == 200
        await decide_all(admin, op)
        assert (await patch(admin, op, status_code="closed")).status_code == 200

    async def test_closing_reports_its_own_audit_entry(self, admin):
        op = await make_operation(admin)
        await decide_all(admin, op)
        await patch(admin, await report_of(admin, op), status_code="no_findings")
        await patch(admin, op, status_code="closed")
        events = await history(admin, op)
        assert any(e["action"] == "status_change" for e in events)
        assert (await get_ticket(admin, op))["status_code"] == "closed"
