"""Seeded roles against the scope's role matrix (Alcance_fase1.md, table of roles).

Maintenance "responds to technical reports" (the corrective action is gated by the field
permission `tickets.edit.technical_report`); Administration "reconciles the period".
"""

import random

from tests.e2e.attachments_support import child_of
from tests.e2e.conftest import API
from tests.e2e.tickets_support import make_operation, ticket_url


class TestMaintenanceAnswersTechnicalReports:
    async def test_the_role_holds_the_per_type_field_permission(self, admin):
        roles = (await admin.get(f"{API}/roles/", params={"limit": 100})).json()["data"]
        role = next(r for r in roles if r["code"] == "maintenance")
        assert "tickets.edit.technical_report" in role["permissions"]

    async def test_maintenance_fills_the_corrective_action_the_pilot_cannot(
        self, maintenance, pilot, ops
    ):
        op = await make_operation(ops)
        report = await child_of(ops, op, "technical_report")
        answer = {"payload": {"corrective_action": "Cambio de filtro"}}
        refused = await pilot.patch(ticket_url(report), json=answer)
        assert refused.status_code == 403
        done = await maintenance.patch(ticket_url(report), json=answer)
        assert done.status_code == 200, done.text
        assert done.json()["payload"]["corrective_action"] == "Cambio de filtro"

    async def test_it_does_not_open_the_other_derived_permissions(self, maintenance):
        me = (await maintenance.get(f"{API}/auth/me/")).json()
        held = set(me["permissions"])
        assert "tickets.edit.technical_report" in held
        assert not {p for p in held if p.startswith("tickets.create.")}


class TestAdministrationReconcilesThePeriod:
    async def test_the_role_holds_the_close_permission_and_the_report_view(self, admin):
        roles = (await admin.get(f"{API}/roles/", params={"limit": 100})).json()["data"]
        role = next(r for r in roles if r["code"] == "admin")
        assert {"reconciliation.close", "analytics.view"} <= set(role["permissions"])

    async def test_administration_reads_the_report_and_may_close(self, accounting):
        # A month no other test writes expenses in (the database is shared across the suite).
        period = f"{random.randint(1950, 1999)}-{random.randint(1, 12):02d}"
        report = await accounting.get(f"{API}/reconciliation/", params={"period": period})
        assert report.status_code == 200, report.text
        closed = await accounting.post(f"{API}/reconciliation/close/", json={"period": period})
        assert closed.status_code in (200, 201), closed.text

    async def test_other_seeded_roles_still_cannot_close(self, pilot, maintenance, ground):
        for api in (pilot, maintenance, ground):
            r = await api.post(f"{API}/reconciliation/close/", json={"period": "1949-04"})
            assert r.status_code == 403
