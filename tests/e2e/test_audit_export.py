"""GET /audit/export/: permission, CSV safety, filters and its own audit event."""

import csv
import io
from datetime import date, timedelta

import pytest

from tests.e2e.conftest import API, create_role
from tests.e2e.live_support import live_server  # noqa: F401  (fixture)

EXPORT = f"{API}/audit/export/"
BOM = b"\xef\xbb\xbf"


def parse(response) -> tuple[list[str], list[dict[str, str]]]:
    assert response.content.startswith(BOM)
    reader = csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";")
    rows = list(reader)
    header, body = rows[0], rows[1:]
    return header, [dict(zip(header, row)) for row in body]


async def exported(api, query: str = ""):
    r = await api.get(f"{EXPORT}?{query}" if query else EXPORT)
    assert r.status_code == 200, r.text
    return parse(r)


class TestPermission:
    async def test_requires_a_session(self, anon):
        assert (await anon.get(EXPORT)).status_code == 401

    async def test_audit_view_is_not_enough(self, new_account):
        api, _, _ = await new_account(["audit.view"])
        assert (await api.get(EXPORT)).status_code == 403

    async def test_audit_export_alone_opens_the_export_but_not_the_listing(self, new_account):
        api, _, _ = await new_account(["audit.export"])
        assert (await api.get(EXPORT)).status_code == 200
        assert (await api.get(f"{API}/audit/")).status_code == 403

    async def test_a_pilot_cannot_export(self, pilot):
        assert (await pilot.get(EXPORT)).status_code == 403

    @pytest.mark.parametrize("method", ["post", "patch", "delete"])
    async def test_the_export_route_only_reads(self, admin, method):
        r = await getattr(admin, method)(EXPORT)
        assert r.status_code in (404, 405)


class TestFile:
    async def test_headers_bom_and_columns(self, admin):
        r = await admin.get(EXPORT)
        assert r.headers["content-type"].startswith("text/csv")
        assert r.headers["content-disposition"].startswith('attachment; filename="bitacora-')
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-content-type-options"] == "nosniff"
        header, _ = parse(r)
        assert header[:3] == ["Secuencia", "Fecha (UTC)", "Actor"]
        assert "Resumen" in header and "Cambios" in header

    async def test_rows_match_the_listing_and_go_oldest_first(self, admin, uniq):
        name = f"Rol {uniq()}"
        await create_role(admin, name, ["tickets.view"])
        _, rows = await exported(admin, "entity=role&limit=1")
        sequences = [int(r["Secuencia"]) for r in rows]
        assert sequences == sorted(sequences)
        listing = (await admin.get(f"{API}/audit/?entity=role&limit=100")).json()
        assert any(r["Etiqueta"] == name for r in rows)
        assert len(rows) >= len(listing["data"])

    async def test_formula_injection_is_neutralized(self, admin, uniq):
        evil = f'=HYPERLINK("http://evil.test/{uniq()}","x")'
        await create_role(admin, evil, ["tickets.view"])
        r = await admin.get(f"{EXPORT}?entity=role")
        _, rows = parse(r)
        cells = [row["Etiqueta"] for row in rows if "evil.test" in row["Etiqueta"]]
        assert cells and all(c.startswith("'=") for c in cells)
        for row in rows:
            for value in row.values():
                assert not value.startswith(("=", "+", "-", "@", "\t", "\r")), value

    async def test_plus_minus_at_and_tab_prefixes_are_neutralized(self, admin, uniq):
        for prefix in ("+", "-", "@"):
            await create_role(admin, f"{prefix}cmd{uniq()}", ["tickets.view"])
        _, rows = await exported(admin, "entity=role&search=cmd")
        labels = [r["Etiqueta"] for r in rows if "cmd" in r["Etiqueta"]]
        assert len(labels) >= 3 and all(label[0] == "'" for label in labels)

    async def test_embedded_quotes_and_newlines_keep_the_columns_intact(self, admin, uniq):
        awkward = f'Rol "con comillas"; y\nsalto {uniq()}'
        await create_role(admin, awkward.replace("\n", " "), ["tickets.view"])
        _, rows = await exported(admin, "entity=role")
        assert any('"con comillas"' in r["Etiqueta"] for r in rows)
        assert len({len(r) for r in rows}) == 1

    async def test_secrets_never_enter_the_file(self, admin):
        text = (await admin.get(EXPORT)).text
        assert "$2b$" not in text and "password_hash" not in text


class TestFilters:
    async def test_entity_and_action(self, admin, uniq):
        await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        _, rows = await exported(admin, "entity=role&action=create")
        assert rows and {r["Entidad"] for r in rows} == {"role"}
        assert {r["Acción"] for r in rows} == {"create"}

    async def test_entity_id_and_actor(self, admin, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        _, rows = await exported(admin, f"entity_id={role['id']}")
        assert rows and {r["Id de la entidad"] for r in rows} == {role["id"]}

    async def test_date_range_is_inclusive_of_the_whole_last_day(self, admin, uniq):
        await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        today = date.today().isoformat()
        _, rows = await exported(admin, f"entity=role&from={today}&to={today}")
        assert rows
        tomorrow = (date.today() + timedelta(days=2)).isoformat()
        _, none = await exported(admin, f"from={tomorrow}")
        assert none == []
        yesterday = (date.today() - timedelta(days=2)).isoformat()
        _, before = await exported(admin, f"to={yesterday}&entity=role")
        assert all(r["Fecha (UTC)"][:10] <= yesterday for r in before)

    @pytest.mark.parametrize(
        "query", ["entity=nope", "action=nope", "from=ayer", "to=2026-13-45", "from=" + "9" * 41]
    )
    async def test_invalid_filters_are_422(self, admin, query):
        assert (await admin.get(f"{EXPORT}?{query}")).status_code == 422

    async def test_unknown_filters_are_ignored_not_a_data_oracle(self, admin):
        r = await admin.get(f"{EXPORT}?password_hash__like=%24")
        assert r.status_code == 200

    async def test_search_looks_inside_the_diff(self, admin, uniq):
        marker = uniq("marca")
        await create_role(admin, f"Rol {marker}", ["tickets.view"])
        _, rows = await exported(admin, f"search={marker}")
        assert rows and all(marker in str(r) for r in rows)


class TestTheExportIsAudited:
    async def test_it_leaves_an_event_with_who_how_many_and_filters(self, admin, uniq):
        await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        await admin.get(f"{EXPORT}?entity=role")
        events = (await admin.get(f"{API}/audit/?action=export&entity=audit")).json()["data"]
        assert events
        newest = events[0]
        assert newest["actor_name"] == "Superusuario" and newest["summary"].startswith("Exportó ")
        fields = {c["field"]: c["to"] for c in newest["changes"]}
        assert fields["rows"] >= 1 and "entity=role" in fields["filters"]

    async def test_a_file_never_contains_its_own_export(self, admin):
        _, first = await exported(admin, "entity=audit")
        _, second = await exported(admin, "entity=audit")
        assert len(second) == len(first) + 1

    async def test_the_denied_export_leaves_no_event(self, admin, pilot):
        before = (await admin.get(f"{API}/audit/?action=export&entity=audit")).json()["total"]
        assert (await pilot.get(EXPORT)).status_code == 403
        after = (await admin.get(f"{API}/audit/?action=export&entity=audit")).json()["total"]
        assert after == before


class TestRowCap:
    async def test_over_the_cap_is_refused_and_a_narrower_range_works(
        self, live_server, new_account  # noqa: F811
    ):
        make = live_server(AUDIT_EXPORT_MAX_ROWS="5")
        _, user, password = await new_account(["audit.export", "audit.view"])
        api = make()
        token = (await api.login(user["email"], password)).json()["token"]
        api = api.as_token(token)
        refused = await api.get(EXPORT)
        assert refused.status_code == 422 and "máximo de 5" in refused.json()["detail"]
        narrow = await api.get(f"{EXPORT}?entity=audit&actor_id=nobody")
        assert narrow.status_code == 200
