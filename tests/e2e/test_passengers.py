"""Manifest of an operation and the search of who travelled where (§6.17)."""

import uuid

from tests.e2e.attachments_support import attach, files_of
from tests.e2e.closing_support import audit_events, closed_operation
from tests.e2e.conftest import API
from tests.e2e.tickets_support import make_expense, make_operation, ticket_url

ZERO = "00000000-0000-0000-0000-000000000000"
MARIA = {"full_name": "María José Pérez", "document": "V-12.345.678", "nationality": "VE"}
JUAN = {"full_name": "Juan Rodríguez", "document": "E 87.654.321"}


def tag() -> str:
    """A name/document token no other test uses (the DB is shared across the suite)."""
    return uuid.uuid4().hex[:10]


def person(name: str | None = None, number: str | None = None, **extra) -> dict:
    return {
        "full_name": name or f"Pasajero {tag()}",
        "document": number or f"V{uuid.uuid4().int % 10**9:09d}",
        **extra,
    }


async def put(api, op, people):
    return await api.request("PUT", ticket_url(op, "passengers/"), json={"passengers": people})


async def manifest(api, op) -> list[dict]:
    r = await api.get(ticket_url(op, "passengers/"))
    assert r.status_code == 200, r.text
    return r.json()


async def search(api, q: str):
    return await api.get(f"{API}/passengers/search/", params={"q": q})


async def found(api, q: str) -> list[dict]:
    r = await search(api, q)
    assert r.status_code == 200, r.text
    return r.json()["data"]


class TestManifest:
    async def test_an_empty_manifest_reads_as_a_list(self, ops):
        assert await manifest(ops, await make_operation(ops)) == []

    async def test_put_replaces_and_returns_the_list(self, ops):
        op = await make_operation(ops)
        r = await put(ops, op, [MARIA, JUAN])
        assert r.status_code == 200
        saved = r.json()
        assert [p["full_name"] for p in saved] == ["María José Pérez", "Juan Rodríguez"]
        assert saved[0]["nationality"] == "VE" and saved[1]["nationality"] is None
        assert all(p["id"] and p["passport_file_id"] is None for p in saved)

    async def test_post_is_the_same_verb_as_the_simulator_uses(self, ops):
        op = await make_operation(ops)
        r = await ops.post(ticket_url(op, "passengers/"), json={"passengers": [MARIA]})
        assert r.status_code == 200 and len(r.json()) == 1

    async def test_the_order_of_the_manifest_is_kept(self, ops):
        op = await make_operation(ops)
        names = [f"Persona {i}" for i in range(6)]
        await put(ops, op, [person(n) for n in names])
        assert [p["full_name"] for p in await manifest(ops, op)] == names

    async def test_a_second_put_replaces_the_first(self, ops):
        op = await make_operation(ops)
        await put(ops, op, [MARIA, JUAN])
        await put(ops, op, [person("Solo Uno")])
        assert [p["full_name"] for p in await manifest(ops, op)] == ["Solo Uno"]

    async def test_an_empty_list_clears_the_manifest(self, ops):
        op = await make_operation(ops)
        await put(ops, op, [MARIA])
        assert (await put(ops, op, [])).status_code == 200
        assert await manifest(ops, op) == []

    async def test_names_are_trimmed_and_collapsed(self, ops):
        op = await make_operation(ops)
        r = await put(ops, op, [person("  Ana    María   Gómez ")])
        assert r.json()[0]["full_name"] == "Ana María Gómez"

    async def test_the_same_document_twice_in_one_manifest_is_422(self, ops):
        op = await make_operation(ops)
        r = await put(ops, op, [person("A", "V-1.234.567"), person("B", "v 1234567")])
        assert r.status_code == 422
        assert await manifest(ops, op) == []

    async def test_a_failed_replace_keeps_the_previous_manifest(self, ops):
        op = await make_operation(ops)
        await put(ops, op, [MARIA])
        r = await put(ops, op, [person("A", "V-1"), person("B", "V1")])
        assert r.status_code == 422
        assert [p["full_name"] for p in await manifest(ops, op)] == ["María José Pérez"]

    async def test_missing_name_or_document_is_422(self, ops):
        op = await make_operation(ops)
        assert (await put(ops, op, [{"document": "V1234"}])).status_code == 422
        assert (await put(ops, op, [{"full_name": "Sin doc"}])).status_code == 422
        assert (await put(ops, op, [{"full_name": "  ", "document": "V12"}])).status_code == 422

    async def test_a_document_of_only_separators_is_422(self, ops):
        op = await make_operation(ops)
        assert (await put(ops, op, [person("Ana", "..--  ")])).status_code == 422

    async def test_oversized_fields_are_422(self, ops):
        op = await make_operation(ops)
        assert (await put(ops, op, [person("x" * 500)])).status_code == 422
        assert (await put(ops, op, [person("Ana", "9" * 100)])).status_code == 422
        assert (await put(ops, op, [person("Ana", nationality="y" * 200)])).status_code == 422

    async def test_more_than_500_passengers_is_422(self, ops):
        op = await make_operation(ops)
        people = [person(f"P {i}", f"V{i:09d}") for i in range(501)]
        assert (await put(ops, op, people)).status_code == 422

    async def test_exactly_500_passengers_is_accepted(self, ops):
        op = await make_operation(ops)
        people = [person(f"P {i}", f"V{i + 1:09d}") for i in range(500)]
        assert (await put(ops, op, people)).status_code == 200
        assert len(await manifest(ops, op)) == 500

    async def test_the_body_must_be_an_object_with_a_list(self, ops):
        op = await make_operation(ops)
        assert (
            await ops.request("PUT", ticket_url(op, "passengers/"), json=[MARIA])
        ).status_code in (400, 422)
        assert (
            await ops.request("PUT", ticket_url(op, "passengers/"), json={"passengers": "x"})
        ).status_code == 422
        assert (await ops.request("PUT", ticket_url(op, "passengers/"), json={})).status_code == 422

    async def test_internal_fields_cannot_be_set_by_the_client(self, ops, admin):
        op = await make_operation(ops)
        r = await put(
            ops,
            op,
            [
                {
                    **MARIA,
                    "id": ZERO,
                    "name_key": "zzz",
                    "document_key": "ZZZ",
                    "ticket_id": ZERO,
                    "position": 99,
                }
            ],
        )
        assert r.status_code == 200
        assert r.json()[0]["id"] != ZERO
        assert len(await found(admin, "V12345678")) >= 1
        assert await found(admin, "zzz") == []

    async def test_unknown_operation_is_404(self, ops):
        assert (await ops.get(ticket_url(ZERO, "passengers/"))).status_code == 404
        assert (await put(ops, ZERO, [MARIA])).status_code == 404

    async def test_a_non_operation_ticket_has_no_manifest(self, ops):
        op = await make_operation(ops)
        expense = await make_expense(ops, op)
        assert (await ops.get(ticket_url(expense, "passengers/"))).status_code == 404
        assert (await put(ops, expense, [MARIA])).status_code == 404

    async def test_a_closed_operation_refuses_changes_with_409_and_audits_it(self, ops, admin):
        op = await closed_operation(ops)
        r = await put(ops, op, [MARIA])
        assert r.status_code == 409
        denied = await audit_events(admin, action="denied")
        assert any(e["entity_id"] == op["id"] for e in denied)

    async def test_a_closed_operation_manifest_stays_readable(self, ops):
        op = await make_operation(ops)
        await put(ops, op, [MARIA])
        op_closed = await closed_operation(ops)
        assert await manifest(ops, op_closed) == []
        assert len(await manifest(ops, op)) == 1

    async def test_replacing_is_audited(self, ops, admin):
        op = await make_operation(ops)
        await put(ops, op, [MARIA, JUAN])
        events = await audit_events(admin, entity_id=op["id"])
        assert any(
            "pasajero" in e["summary"].lower() or "manifiesto" in e["summary"].lower()
            for e in events
        )

    async def test_anonymous_gets_401(self, anon, ops):
        op = await make_operation(ops)
        assert (await anon.get(ticket_url(op, "passengers/"))).status_code == 401
        assert (await put(anon, op, [MARIA])).status_code == 401


class TestManifestPermissions:
    async def test_a_viewer_may_read_but_not_write(self, new_account, ops):
        viewer, _, _ = await new_account(["tickets.view.operation"])
        op = await make_operation(ops)
        await put(ops, op, [MARIA])
        assert len(await manifest(viewer, op)) == 1
        r = await put(viewer, op, [JUAN])
        assert r.status_code == 403
        assert len(await manifest(ops, op)) == 1

    async def test_a_role_that_cannot_view_operations_gets_404(self, new_account, ops):
        outsider, _, _ = await new_account(["tickets.view.expense"])
        op = await make_operation(ops)
        await put(ops, op, [MARIA])
        r = await outsider.get(ticket_url(op, "passengers/"))
        assert r.status_code == 404 and "María" not in r.text
        assert (await put(outsider, op, [JUAN])).status_code in (403, 404)

    async def test_an_editor_of_another_type_cannot_write_the_manifest(self, new_account, ops):
        other, _, _ = await new_account(["tickets.view", "tickets.edit.expense"])
        op = await make_operation(ops)
        assert (await put(other, op, [MARIA])).status_code in (403, 404)
        assert await manifest(ops, op) == []

    async def test_no_ticket_permission_at_all_is_403(self, new_account, ops):
        nobody, _, _ = await new_account(["notifications.view", "passengers.view"])
        op = await make_operation(ops)
        assert (await nobody.get(ticket_url(op, "passengers/"))).status_code == 403


class TestSearch:
    async def test_the_document_format_does_not_matter_in_either_direction(self, admin):
        number = f"{uuid.uuid4().int % 10**8:08d}"
        a, b = await make_operation(admin), await make_operation(admin)
        await put(admin, a, [person("Pax A", f"V{number}")])
        await put(admin, b, [person("Pax A", f"v-{number[:2]}.{number[2:5]}.{number[5:]}")])
        for query in (f"V-{number}", f"v {number}", f"V{number}", number):
            hits = await found(admin, query)
            assert len(hits) == 1, query
            assert sorted(o["id"] for o in hits[0]["operations"]) == sorted([a["id"], b["id"]])

    async def test_the_name_is_searched_without_accents_or_case(self, admin):
        t = tag()
        op = await make_operation(admin)
        await put(admin, op, [person(f"María José {t}")])
        assert len(await found(admin, f"maria jose {t}")) == 1
        assert len(await found(admin, f"MARÍA JOSÉ {t.upper()}")) == 1
        assert len(await found(admin, t)) == 1

    async def test_the_query_may_carry_accents_the_record_lacks(self, admin):
        t = tag()
        await put(admin, await make_operation(admin), [person(f"Jose Peña {t}")])
        assert len(await found(admin, f"JOSÉ PEÑA {t}")) == 1

    async def test_each_hit_carries_code_date_route_and_status(self, admin):
        t = tag()
        op = await make_operation(admin)
        await put(admin, op, [person(f"Viajero {t}")])
        [hit] = (await found(admin, t))[0]["operations"]
        assert hit["id"] == op["id"] and hit["code"] == op["code"]
        assert hit["status_code"] == op["status_code"] and hit["status_name"]
        assert hit["date"] == "2026-08-19" and hit["route"] == "CCS → PZO"
        assert hit["status_is_final"] is False

    async def test_no_match_is_an_empty_list_not_an_error(self, admin):
        r = await search(admin, "zzzz-inexistente-" + tag())
        assert r.status_code == 200 and r.json() == {"data": []}

    async def test_blank_or_missing_query_lists_the_most_recent_people(self, admin):
        t = tag()
        await put(admin, await make_operation(admin, date="2099-01-01"), [person(f"Reciente {t}")])
        for q in ("", "   "):
            hits = await found(admin, q)
            assert len(hits) <= 50 and f"Reciente {t}" in [h["full_name"] for h in hits]
        r = await admin.get(f"{API}/passengers/search/")
        assert r.status_code == 200 and len(r.json()["data"]) <= 50

    async def test_a_short_name_does_not_leak_documents_that_contain_the_letters(self, admin):
        await put(admin, await make_operation(admin), [person("Otro Nombre", "ANA123456")])
        assert all("ANA123456" != h["document"] for h in await found(admin, "ana"))

    async def test_two_different_people_stay_apart(self, admin):
        t = tag()
        await put(admin, await make_operation(admin), [person(f"Rodríguez {t}"), person(f"Pérez {t}")])
        hits = await found(admin, f"rodriguez {t}")
        assert len(hits) == 1 and hits[0]["full_name"].startswith("Rodríguez")

    async def test_the_same_person_on_many_operations_is_one_result(self, admin):
        number = f"{uuid.uuid4().int % 10**8:08d}"
        for _ in range(3):
            await put(admin, await make_operation(admin), [person("Viajero Frecuente", f"V{number}")])
        hits = await found(admin, number)
        assert len(hits) == 1 and len(hits[0]["operations"]) == 3

    async def test_most_recent_operation_first(self, admin):
        number = f"{uuid.uuid4().int % 10**8:08d}"
        old = await make_operation(admin, date="2026-01-05")
        new = await make_operation(admin, date="2026-06-05")
        await put(admin, old, [person("Pax", f"V{number}")])
        await put(admin, new, [person("Pax", f"V{number}")])
        dates = [o["date"] for o in (await found(admin, number))[0]["operations"]]
        assert dates == sorted(dates, reverse=True)

    async def test_results_are_capped_at_50_people(self, admin):
        t = tag()
        op = await make_operation(admin)
        await put(admin, op, [person(f"Zeta {t} {i}", f"V{i + 1:09d}") for i in range(60)])
        assert len(await found(admin, f"zeta {t}")) == 50

    async def test_a_query_over_100_characters_is_422(self, admin):
        assert (await search(admin, "a" * 101)).status_code == 422

    async def test_removed_passengers_disappear_from_the_search(self, admin):
        t = tag()
        op = await make_operation(admin)
        await put(admin, op, [person(f"Fugaz {t}")])
        await put(admin, op, [])
        assert await found(admin, f"fugaz {t}") == []

    async def test_anonymous_gets_401(self, anon):
        assert (await anon.get(f"{API}/passengers/search/?q=maria")).status_code == 401


class TestWhoMaySearch:
    """Looking people up across operations is granted per role. Operaciones does not have it."""

    async def test_operaciones_cannot_search_across_operations(self, ops):
        assert (await search(ops, "maria")).status_code == 403

    async def test_reading_tickets_is_not_enough_to_search_people(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        assert (await search(api, "maria")).status_code == 403

    async def test_the_permission_alone_opens_the_search_within_the_readable_scope(
        self, ops, new_account
    ):
        t = tag()
        await put(ops, await make_operation(ops), [person(f"Alcance {t}")])
        api, _, _ = await new_account(["passengers.view", "tickets.view"])
        assert [h["full_name"] for h in await found(api, f"alcance {t}")] == [f"Alcance {t}"]

    async def test_the_permission_without_reading_operations_is_still_a_404(self, new_account):
        api, _, _ = await new_account(["passengers.view"])
        assert (await search(api, "maria")).status_code == 404

    async def test_the_pilot_role_does_not_have_it_by_default(self, pilot):
        assert (await search(pilot, "maria")).status_code == 403


class TestWildcardAndInjectionAbuse:
    async def test_percent_and_underscore_are_literal_not_wildcards(self, admin):
        await put(admin, await make_operation(admin), [person("Maria Pax", "V99887766")])
        for q in ("%", "%%%", "_", "____", "%_%", "m%a", "_aria"):
            assert await found(admin, q) == [], q

    async def test_a_literal_percent_in_a_name_is_found_as_is(self, admin):
        t = tag()
        await put(admin, await make_operation(admin), [person(f"Cien% Real {t}")])
        assert len(await found(admin, f"cien% real {t}")) == 1
        assert await found(admin, f"cien_ real {t}") == []

    async def test_regex_syntax_is_text_never_a_pattern(self, admin):
        await put(admin, await make_operation(admin), [person("aaaaaaaaaaaaaaaaaaaaaaaaaaaa!")])
        for q in (".*(", "(a+)+$", "[", "\\", "$where", "(?<=", ".*", ".*.*.*.*.*x", "|", "^"):
            r = await search(admin, q)
            assert r.status_code == 200, q
            assert r.json()["data"] == [], q

    async def test_sql_metacharacters_are_inert(self, admin):
        t = tag()
        await put(admin, await make_operation(admin), [person(f"Real {t}")])
        for q in ("' OR '1'='1", "'; DROP TABLE ticket_passenger;--", '" OR 1=1 --', "\\' OR 1=1"):
            r = await search(admin, q)
            assert r.status_code == 200 and r.json()["data"] == [], q
        assert len(await found(admin, f"real {t}")) == 1

    async def test_null_bytes_and_control_chars_do_not_500(self, admin):
        for q in ("a\x00b", "\x01\x02", "‮"):
            r = await admin.get(f"{API}/passengers/search/", params={"q": q})
            assert r.status_code in (200, 422), q

    async def test_a_wildcard_document_cannot_enumerate_passports(self, admin):
        await put(admin, await make_operation(admin), [person("Doc", "V5551234")])
        assert await found(admin, "V%") == []
        assert await found(admin, "%1234") == []

    async def test_the_response_never_contains_the_table_internals(self, admin):
        t = tag()
        await put(admin, await make_operation(admin), [person(f"Interno {t}")])
        text = (await search(admin, t)).text
        assert (
            "name_key" not in text
            and "document_key" not in text
            and 'ticket_id":"' not in text.split("passport")[0]
        )


class TestSearchScope:
    async def test_a_role_that_reads_no_operation_gets_404_and_no_data(self, new_account, ops):
        t = tag()
        await put(ops, await make_operation(ops), [person(f"Secreto {t}")])
        outsider, _, _ = await new_account(["tickets.view.expense", "passengers.view"])
        r = await search(outsider, f"secreto {t}")
        assert r.status_code == 404 and "Secreto" not in r.text

    async def test_a_role_with_no_ticket_permission_at_all_gets_404(self, new_account, ops):
        t = tag()
        await put(ops, await make_operation(ops), [person(f"Privado {t}")])
        nobody, _, _ = await new_account(["notifications.view", "passengers.view"])
        r = await search(nobody, f"privado {t}")
        assert r.status_code == 404 and "Privado" not in r.text

    async def test_a_role_that_reads_operations_sees_the_results(self, new_account, ops):
        t = tag()
        await put(ops, await make_operation(ops), [person(f"Visible {t}")])
        viewer, _, _ = await new_account(["tickets.view.operation", "passengers.view"])
        assert len(await found(viewer, f"visible {t}")) == 1

    async def test_global_view_sees_the_results(self, new_account, ops):
        t = tag()
        await put(ops, await make_operation(ops), [person(f"Global {t}")])
        viewer, _, _ = await new_account(["tickets.view", "passengers.view"])
        assert len(await found(viewer, f"global {t}")) == 1

    async def test_reading_another_type_does_not_open_operations(self, new_account, ops):
        t = tag()
        await put(ops, await make_operation(ops), [person(f"Ajeno {t}")])
        reader, _, _ = await new_account(
            ["tickets.view.expense", "tickets.view.technical_report", "passengers.view"]
        )
        assert (await search(reader, f"ajeno {t}")).status_code == 404

    async def test_the_404_for_no_scope_looks_like_the_404_of_a_missing_thing(
        self, new_account, ops
    ):
        outsider, _, _ = await new_account(["tickets.view.expense", "passengers.view"])
        r = await search(outsider, "x")
        assert (
            r.json().keys() == (await outsider.get(ticket_url(ZERO, "passengers/"))).json().keys()
        )


class TestCrossScope:
    """A user without access to the operation type sees a 404 everywhere, never a 403 and never
    a trace of the manifest (the same answer a missing id gives)."""

    OUTSIDERS = (
        ["tickets.view.expense", "tickets.edit.expense"],
        # Editing the type without being able to read it is not a way in.
        ["tickets.edit.operation", "tickets.view.expense"],
        ["tickets.view.technical_report", "tickets.edit.inac_permit"],
    )

    async def _foreign_manifest(self, ops):
        t = tag()
        op = await make_operation(ops)
        assert (await put(ops, op, [person(f"Reservado {t}", f"V{t}")])).status_code == 200
        return op, t

    async def test_get_put_and_post_are_404_for_every_outsider(self, new_account, ops):
        op, t = await self._foreign_manifest(ops)
        for permissions in self.OUTSIDERS:
            outsider, _, _ = await new_account(permissions)
            url = ticket_url(op, "passengers/")
            for method in ("GET", "PUT", "POST"):
                r = await outsider.request(
                    method, url, json=None if method == "GET" else {"passengers": [JUAN]}
                )
                assert r.status_code == 404, (permissions, method, r.status_code, r.text)
                assert t not in r.text and "Reservado" not in r.text
        assert [p["full_name"] for p in await manifest(ops, op)] == [f"Reservado {t}"]

    async def test_the_404_is_identical_to_the_one_of_a_missing_operation(self, new_account, ops):
        op, _ = await self._foreign_manifest(ops)
        outsider, _, _ = await new_account(self.OUTSIDERS[0])
        for method in ("GET", "PUT"):
            body = None if method == "GET" else {"passengers": [JUAN]}
            real = await outsider.request(method, ticket_url(op, "passengers/"), json=body)
            ghost = await outsider.request(method, ticket_url(ZERO, "passengers/"), json=body)
            assert real.status_code == ghost.status_code == 404
            assert real.json() == ghost.json()

    async def test_search_is_404_and_leaks_nothing_for_every_outsider(self, new_account, ops):
        _, t = await self._foreign_manifest(ops)
        for permissions in self.OUTSIDERS:
            outsider, _, _ = await new_account([*permissions, "passengers.view"])
            for query in (f"reservado {t}", f"V{t}", "a"):
                r = await search(outsider, query)
                assert r.status_code == 404, (permissions, query, r.status_code)
                assert t not in r.text and "Reservado" not in r.text

    async def test_search_404_matches_the_missing_thing_404(self, new_account, ops):
        outsider, _, _ = await new_account([*self.OUTSIDERS[0], "passengers.view"])
        denied = await search(outsider, "maria")
        ghost = await outsider.get(ticket_url(ZERO, "passengers/"))
        assert denied.status_code == ghost.status_code == 404
        assert denied.json().keys() == ghost.json().keys()

    async def test_an_insider_still_works_with_the_same_requests(self, new_account, ops):
        op, t = await self._foreign_manifest(ops)
        insider, _, _ = await new_account(
            ["tickets.view.operation", "tickets.edit.operation", "passengers.view"]
        )
        assert len(await manifest(insider, op)) == 1
        assert (await put(insider, op, [JUAN])).status_code == 200
        assert (await search(insider, "juan rodriguez")).status_code == 200


class TestPassport:
    async def _passport(self, api, op):
        await attach(api, op, "pasaporte.png", field_key="passport")
        return (await files_of(api, op))[-1]

    async def test_a_passport_upload_is_classified_as_pasaporte(self, ops):
        file = await self._passport(ops, await make_operation(ops))
        assert file["document_type"] == "pasaporte"

    async def test_the_passenger_keeps_a_reference_to_it(self, ops):
        op = await make_operation(ops)
        file = await self._passport(ops, op)
        r = await put(ops, op, [{**MARIA, "passport_file_id": file["id"]}])
        assert r.status_code == 200 and r.json()[0]["passport_file_id"] == file["id"]
        assert (await manifest(ops, op))[0]["passport_file_id"] == file["id"]

    async def test_the_search_result_carries_the_passport_reference(self, ops, admin):
        number = f"{uuid.uuid4().int % 10**8:08d}"
        op = await make_operation(ops)
        file = await self._passport(ops, op)
        await put(ops, op, [person("Con pasaporte", f"V{number}", passport_file_id=file["id"])])
        hit = (await found(admin, number))[0]
        assert hit["passport"] == {"ticket_id": op["id"], "file_id": file["id"]}

    async def test_no_passport_means_null(self, ops, admin):
        number = f"{uuid.uuid4().int % 10**8:08d}"
        await put(ops, await make_operation(ops), [person("Sin pasaporte", f"V{number}")])
        assert (await found(admin, number))[0]["passport"] is None

    async def test_only_a_reference_is_stored_never_the_image(self, ops):
        op = await make_operation(ops)
        file = await self._passport(ops, op)
        await put(ops, op, [{**MARIA, "passport_file_id": file["id"]}])
        assert "data:image" not in (await ops.get(ticket_url(op, "passengers/"))).text

    async def test_a_file_of_another_operation_cannot_be_linked(self, ops):
        mine, theirs = await make_operation(ops), await make_operation(ops)
        foreign = await self._passport(ops, theirs)
        r = await put(ops, mine, [{**MARIA, "passport_file_id": foreign["id"]}])
        assert r.status_code == 422
        assert await manifest(ops, mine) == []

    async def test_an_unknown_file_id_cannot_be_linked(self, ops):
        op = await make_operation(ops)
        r = await put(ops, op, [{**MARIA, "passport_file_id": str(uuid.uuid4())}])
        assert r.status_code == 422

    async def test_a_malformed_file_id_is_422(self, ops):
        op = await make_operation(ops)
        assert (await put(ops, op, [{**MARIA, "passport_file_id": "no-uuid"}])).status_code == 422

    async def test_a_removed_file_cannot_be_linked(self, ops):
        op = await make_operation(ops)
        file = await self._passport(ops, op)
        await ops.delete(ticket_url(op, f"attachments/{file['id']}/"))
        r = await put(ops, op, [{**MARIA, "passport_file_id": file["id"]}])
        assert r.status_code == 422

    async def test_the_download_link_comes_from_the_attachments_endpoint(self, ops):
        op = await make_operation(ops)
        file = await self._passport(ops, op)
        link = await ops.get(ticket_url(op, f"attachments/{file['id']}/download/"))
        assert link.status_code == 200 and link.json()["url"]

    async def test_a_foreign_passport_never_leaks_through_the_search(self, ops, admin):
        number = f"{uuid.uuid4().int % 10**8:08d}"
        mine, theirs = await make_operation(ops), await make_operation(ops)
        foreign = await self._passport(ops, theirs)
        await put(ops, mine, [person("Intruso", f"V{number}", passport_file_id=foreign["id"])])
        hits = await found(admin, number)
        assert hits == [] or hits[0]["passport"] is None
