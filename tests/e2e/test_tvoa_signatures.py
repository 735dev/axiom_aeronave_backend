"""TVOA closing: signing steps are data, and the role AVSEC owns its part of the circuit."""

from tests.e2e.closing_support import (
    SIGNING_STEPS,
    close_operation,
    closing,
    decide,
    sign_step,
    technical_reports,
)
from tests.e2e.conftest import API, role_by_code, role_id
from tests.e2e.tickets_support import (
    TICKETS,
    field,
    get_ticket,
    make_operation,
    patch,
    status,
    type_body,
    type_by_code,
)

TYPES = f"{API}/ticket-types/"


def codes(role: dict) -> set[str]:
    return {p["code"] if isinstance(p, dict) else p for p in role["permissions"]}


class TestWhichStepsSignIsData:
    async def test_the_flag_travels_in_the_type_definition(self, admin):
        operation = await type_by_code(admin, "operation")
        assert [s["code"] for s in operation["steps"] if s["requires_signature"]] == list(
            SIGNING_STEPS
        )
        technical = await type_by_code(admin, "technical_report")
        assert not any(s["requires_signature"] for s in technical["steps"])

    async def test_a_new_root_type_closes_only_with_its_own_signatures(self, admin, pilot):
        steps = [
            {
                "code": "sign_pilot",
                "name": "Firma del piloto",
                "role_id": await role_id(admin, "pilot"),
                "requires_signature": True,
            }
        ]
        body = type_body(
            is_root=True,
            fields=[field("detail", "Detalle", "text")],
            statuses=[
                status("open", "Abierto", is_initial=True),
                status("closed", "Cerrado", is_final=True),
            ],
            steps=steps,
        )
        created = await admin.post(TYPES, json=body)
        assert created.status_code == 201, created.text
        assert created.json()["steps"][0]["requires_signature"] is True
        r = await admin.post(
            TICKETS,
            json={"type_code": body["code"], "title": "Propio", "payload": {"detail": "x"}},
        )
        assert r.status_code == 201, r.text
        ticket = r.json()

        refused = await close_operation(admin, ticket)
        assert refused.status_code == 422
        assert "faltan las firmas de Piloto" in refused.json()["detail"]

        assert (await sign_step_as(pilot, ticket, "sign_pilot")).status_code == 201
        assert (await close_operation(admin, ticket)).status_code == 200
        signatures = (await closing(admin, ticket))["stamp"]["signatures"]
        assert [s["step_code"] for s in signatures] == ["sign_pilot"]

    async def test_a_type_can_stop_requiring_a_signature(self, admin):
        steps = [{"code": "check", "name": "Revisar", "requires_signature": True}]
        created = (await admin.post(TYPES, json=type_body(steps=steps))).json()
        assert created["steps"][0]["requires_signature"] is True
        r = await admin.patch(
            f"{TYPES}{created['id']}/",
            json={"steps": [{"code": "check", "name": "Revisar", "requires_signature": False}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["steps"][0]["requires_signature"] is False

    async def test_the_flag_defaults_to_false(self, admin):
        created = (
            await admin.post(TYPES, json=type_body(steps=[{"code": "c", "name": "Revisar"}]))
        ).json()
        assert created["steps"][0]["requires_signature"] is False


async def sign_step_as(api, ticket, step):
    """The given session signs `step` (it must hold the step's role and a loaded signature)."""
    from tests.e2e.closing_support import set_signature

    await set_signature(api)
    return await decide(api, ticket, step)


class TestAvsecRole:
    async def test_exists_with_a_narrow_set_of_permissions(self, admin):
        role = await role_by_code(admin, "avsec")
        assert role["name"] == "Coordinación AVSEC"
        granted = codes(role)
        assert {"tickets.view", "tickets.edit", "tickets.sign", "notifications.view"} <= granted
        assert not granted & {
            "users.manage",
            "roles.manage",
            "catalogs.manage",
            "ticket_types.manage",
            "tickets.decide_any",
            "reconciliation.close",
            "audit.view",
        }

    async def test_it_cannot_reach_what_is_outside_its_grants(self, avsec):
        assert (await avsec.get(f"{API}/audit/")).status_code == 403
        assert (await avsec.get(f"{API}/users/")).status_code == 403
        assert (await avsec.post(f"{API}/roles/", json={"name": "Otro"})).status_code == 403
        assert (await avsec.post(TYPES, json=type_body())).status_code == 403
        assert (
            await avsec.post(
                f"{API}/catalog-items/", json={"catalog": "locations", "name": "Sitio"}
            )
        ).status_code == 403
        assert (await avsec.get(f"{API}/reconciliation/?period=2001-01")).status_code == 403

    async def test_it_reads_the_operations_it_has_to_sign(self, avsec, ops):
        op = await make_operation(ops)
        assert (await get_ticket(avsec, op))["id"] == op["id"]

    async def test_it_owns_the_workshop_transfer_and_return_steps(self, avsec, ops):
        technical = await type_by_code(avsec, "technical_report")
        owners = {s["code"]: (s["role_name"], s["responsible_area"]) for s in technical["steps"]}
        assert owners["movement"] == ("Coordinación AVSEC", "Coord. AVSEC")
        assert owners["return"] == ("Coordinación AVSEC", "Coord. AVSEC")
        assert owners["diagnosis"][0] == "Mantenimiento"
        assert owners["airworthiness"][0] == "Mantenimiento"

    async def test_only_that_role_or_decide_any_records_those_steps(
        self, avsec, ops, pilot, maintenance
    ):
        op = await make_operation(ops)
        report = (await technical_reports(ops, op))[0]
        assert (await decide(avsec, report, "movement")).status_code == 201
        assert (await decide(avsec, report, "return")).status_code == 201
        assert (await decide(pilot, report, "movement")).status_code == 403
        assert (await decide(maintenance, report, "return")).status_code == 403
        # Operaciones used to own them: decide_any still lets management step in.
        assert (await decide(ops, report, "movement")).status_code == 201

    async def test_it_signs_only_its_own_signature(self, avsec, ops):
        op = await make_operation(ops)
        assert (await decide(avsec, op, "sign_general")).status_code == 403
        assert (await decide(avsec, op, "sign_captain")).status_code == 403
        assert (await sign_step(ops, op, "sign_avsec")).status_code == 201
        assert (await patch(ops, op, title="otro")).status_code == 200


class TestOperationsNoLongerSigns:
    async def test_operaciones_keeps_planning_and_loses_the_closing_signature(self, ops):
        operation = await type_by_code(ops, "operation")
        owners = {s["code"]: s["role_name"] for s in operation["steps"]}
        assert owners["planning"] == "Gerencia de Operaciones"
        assert "Gerencia de Operaciones" not in {
            s["role_name"] for s in operation["steps"] if s["requires_signature"]
        }
