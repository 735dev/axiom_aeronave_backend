import asyncio
import re

import pytest

from tests.e2e.tickets_support import TICKETS, make_expense, make_operation, post_expense

OP_CODE = re.compile(r"^([A-Z0-9]+)-(\d{3,})-(\d{2})$")


def number_of(code: str) -> int:
    return int(code.split("-")[1])


class TestTicketCodes:
    async def test_children_use_the_simple_sequence(self, admin):
        exp = await make_expense(admin, await make_operation(admin))
        assert re.fullmatch(r"TK-\d{4,}", exp["code"])

    async def test_fifty_creations_never_repeat_a_code(self, admin):
        op = await make_operation(admin)
        results = [await post_expense(admin, op) for _ in range(50)]
        codes = [r.json()["code"] for r in results]
        assert len(set(codes)) == 50

    async def test_codes_increase(self, admin):
        op = await make_operation(admin)
        a = await make_expense(admin, op)
        b = await make_expense(admin, op)
        assert int(b["code"][3:]) > int(a["code"][3:])

    async def test_a_new_code_collides_with_no_existing_one(self, admin):
        existing = {t["code"] for t in (await admin.get(f"{TICKETS}?limit=100")).json()["data"]}
        exp = await make_expense(admin, await make_operation(admin))
        assert exp["code"] not in existing

    async def test_concurrent_creations_get_distinct_codes(self, admin):
        op = await make_operation(admin)
        responses = await asyncio.gather(*(post_expense(admin, op) for _ in range(12)))
        assert all(r.status_code == 201 for r in responses)
        codes = [r.json()["code"] for r in responses]
        assert len(set(codes)) == 12

    async def test_client_code_is_ignored(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "expense",
                "parent_id": op["id"],
                "code": "TK-0001",
                "payload": {"concept": "fuel", "amount": 1, "currency": "USD"},
            },
        )
        assert r.json()["code"] != "TK-0001"

    async def test_a_rejected_creation_does_not_reuse_a_code_for_a_different_ticket(self, admin):
        op = await make_operation(admin)
        first = await make_expense(admin, op)
        await post_expense(admin, op, amount="mal")
        second = await make_expense(admin, op)
        assert second["code"] != first["code"]


class TestOperationCodes:
    async def test_format_is_registration_sequence_year(self, admin):
        op = await make_operation(admin)
        match = OP_CODE.match(op["code"])
        assert match and match.group(1) == "YV2323" and match.group(3) == "26"

    async def test_sequence_is_per_aircraft(self, admin):
        a1 = await make_operation(admin, aircraft="YV2528")
        a2 = await make_operation(admin, aircraft="YV2528")
        other = await make_operation(admin, aircraft="YV2822")
        assert number_of(a2["code"]) == number_of(a1["code"]) + 1
        assert other["code"].startswith("YV2822-")

    async def test_sequence_restarts_each_year(self, admin):
        y26 = await make_operation(admin, date="2026-05-01")
        y27 = await make_operation(admin, date="2027-05-01")
        assert y26["code"].endswith("-26") and y27["code"].endswith("-27")
        assert number_of(y27["code"]) >= 1
        again = await make_operation(admin, date="2027-06-01")
        assert number_of(again["code"]) == number_of(y27["code"]) + 1

    async def test_year_comes_from_the_accounting_date(self, admin):
        op = await make_operation(admin, date="2031-12-31")
        assert op["code"].endswith("-31")

    async def test_each_aircraft_starts_at_001(self, admin):
        op = await make_operation(admin, aircraft="YV2822", date="2040-01-01")
        assert op["code"] == "YV2822-001-40"

    async def test_concurrent_operations_get_distinct_codes(self, admin):
        results = await asyncio.gather(
            *(make_operation(admin, aircraft="YV2822", date="2041-01-01") for _ in range(8))
        )
        codes = [r["code"] for r in results]
        assert len(set(codes)) == 8
        assert sorted(number_of(c) for c in codes) == list(range(1, 9))

    @pytest.mark.parametrize("bad", ["yv-2323", "YV 2323", "YV2323!", ""])
    async def test_invalid_registration_is_rejected(self, admin, bad):
        body = {
            "type_code": "operation",
            "payload": {
                "date": "2026-08-19",
                "origin": "CCS",
                "destination": "PZO",
                "aircraft": bad,
            },
        }
        r = await admin.post(TICKETS, json=body)
        assert r.status_code == 422

    async def test_control_number_is_sequential_per_year(self, admin):
        a = await make_operation(admin, date="2042-01-01", aircraft="YV2822")
        b = await make_operation(admin, date="2042-01-02", aircraft="YV2822")
        assert a["control_number"].endswith("-42") and b["control_number"].endswith("-42")
        assert a["control_number"].startswith("TVOA N°")
        assert int(b["control_number"][7:11]) == int(a["control_number"][7:11]) + 1

    async def test_control_number_is_stored_in_the_payload(self, admin):
        op = await make_operation(admin)
        assert op["payload"]["control_number"] == op["control_number"]

    async def test_control_number_cannot_be_forced_through_the_payload(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "operation",
                "payload": {
                    "date": "2026-08-19",
                    "origin": "CCS",
                    "destination": "PZO",
                    "aircraft": "YV2323",
                    "control_number": "TVOA N°0000-00",
                },
            },
        )
        assert r.status_code in (201, 422)
        if r.status_code == 201:
            assert r.json()["control_number"] != "TVOA N°0000-00"
            assert r.json()["control_number"] != op["control_number"]

    async def test_children_of_an_operation_do_not_consume_operation_numbers(self, admin):
        a = await make_operation(admin, aircraft="YV2528", date="2043-01-01")
        for _ in range(3):
            await make_expense(admin, a)
        b = await make_operation(admin, aircraft="YV2528", date="2043-01-02")
        assert number_of(b["code"]) == number_of(a["code"]) + 1

    async def test_root_without_aircraft_falls_back_to_ticket_codes(self, admin):
        r = await admin.post(
            "/api/v1/ticket-types/",
            json={
                "name": "Raiz sin avion",
                "code": "raiz_sin_avion",
                "is_root": True,
                "fields": [{"label": "Detalle", "type": "text", "key": "detail"}],
                "statuses": [
                    {"code": "a", "name": "A", "is_initial": True},
                    {"code": "z", "name": "Z", "is_final": True},
                ],
            },
        )
        assert r.status_code == 201, r.text
        made = await admin.post(TICKETS, json={"type_code": "raiz_sin_avion", "payload": {}})
        assert made.status_code == 201, made.text
        assert re.fullmatch(r"TK-\d{4,}", made.json()["code"])
