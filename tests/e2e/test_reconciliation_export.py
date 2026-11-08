"""CSV export of the reconciliation detail: permission, headers, injection, audit."""

import csv
import io

from tests.e2e.closing_support import (
    acknowledge,
    audit_events,
    dated_expense,
    fresh_month,
    justify_all,
    report,
    validated_expense,
)
from tests.e2e.conftest import API
from tests.e2e.tickets_support import make_operation, patch, post_expense

BOM = "﻿"


async def export(api, period):
    return await api.get(f"{API}/reconciliation/export/?period={period}")


def parse(text: str) -> list[list[str]]:
    assert text.startswith(BOM)
    return list(csv.reader(io.StringIO(text[1:]), delimiter=";", quotechar='"'))


class TestEnvelope:
    async def test_it_is_text_csv_with_a_filename(self, admin):
        r = await export(admin, "2020-05")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "conciliacion-2020-05.csv" in r.headers["content-disposition"]
        assert "attachment" in r.headers["content-disposition"]

    async def test_it_starts_with_the_utf8_bom(self, admin):
        raw = (await export(admin, "2020-05")).content
        assert raw.startswith(b"\xef\xbb\xbf")

    async def test_it_is_never_cached_nor_sniffed(self, admin):
        r = await export(admin, "2020-05")
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-content-type-options"] == "nosniff"

    async def test_an_empty_month_is_header_only_with_15_columns(self, admin):
        rows = parse((await export(admin, "2020-05")).text)
        assert len(rows) == 1 and len(rows[0]) == 15 and rows[0][0] == "Gasto"

    async def test_lines_end_with_crlf_and_use_semicolons(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        text = (await export(admin, period)).text
        assert text.endswith("\r\n") and text.count("\r\n") == 2 and ";" in text

    async def test_bad_period_is_422(self, admin):
        assert (await export(admin, "junio")).status_code == 422
        assert (await admin.get(f"{API}/reconciliation/export/")).status_code == 422

    async def test_accents_survive_the_roundtrip(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        assert "Operación" in (await export(admin, period)).text


class TestContent:
    async def test_one_line_per_expense_with_state_and_differences_in_spanish(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin)
        await validated_expense(admin, op, 100, expense_date=day)
        await dated_expense(admin, op, day, 40)
        rows = parse((await export(admin, period)).text)
        assert len(rows) == 3
        states = sorted(r[12] for r in rows[1:])
        assert states == ["Con diferencia", "Conciliado"]
        assert any("Gasto sin soporte" in r[13] for r in rows[1:])

    async def test_amounts_use_a_decimal_comma(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day, 1850.5)
        row = parse((await export(admin, period)).text)[1]
        assert row[6] == "1850,50" and row[8] == "1850,50" and row[7] == "USD"

    async def test_receipt_amount_is_empty_without_a_validated_receipt(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        row = parse((await export(admin, period)).text)[1]
        assert row[10] == "" and row[11] == "" and row[9] == "0"

    async def test_validated_receipt_amount_is_exported(self, admin):
        period, day = fresh_month()
        await validated_expense(admin, await make_operation(admin), 77.25, expense_date=day)
        row = parse((await export(admin, period)).text)[1]
        assert row[10] == "77,25" and row[11] == "USD" and row[9] == "1"

    async def test_the_justification_is_exported(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        await acknowledge(admin, period, key, 'Dijo "ok"; luego, nada')
        row = parse((await export(admin, period)).text)[1]
        assert row[14] == 'Dijo "ok"; luego, nada'

    async def test_the_operation_code_and_date_are_exported(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin)
        await dated_expense(admin, op, day)
        row = parse((await export(admin, period)).text)[1]
        assert row[1] == op["code"] and row[4] == day


class TestFormulaInjection:
    async def _title(self, admin, title):
        period, day = fresh_month()
        op = await make_operation(admin)
        r = await post_expense(admin, op, title=title, expense_date=day)
        assert r.status_code == 201, r.text
        return parse((await export(admin, period)).text)[1]

    async def test_equals_sign_title_is_defanged(self, admin):
        row = await self._title(admin, '=HYPERLINK("http://evil.test","x")')
        assert row[2].startswith("'=")

    async def test_plus_minus_at_titles_are_defanged(self, admin):
        for payload in ("+1+1", "-2+3", "@SUM(A1)"):
            assert (await self._title(admin, payload))[2].startswith("'")

    async def test_a_tab_or_cr_prefix_is_defanged(self, admin):
        row = await self._title(admin, "\t=1+1")
        assert row[2].startswith("'")

    async def test_no_cell_of_the_raw_text_starts_with_a_formula_char(self, admin):
        period, day = fresh_month()
        op = await make_operation(admin)
        await post_expense(admin, op, title="=cmd|' /C calc'!A0", expense_date=day)
        text = (await export(admin, period)).text
        assert ';"=' not in text and '\r\n"=' not in text

    async def test_the_justification_is_defanged_too(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        key = (await report(admin, period))["differences"][0]["key"]
        await acknowledge(admin, period, key, "=1+1 mala nota")
        assert parse((await export(admin, period)).text)[1][14].startswith("'=")

    async def test_embedded_newlines_and_quotes_do_not_break_the_columns(self, admin):
        row = await self._title(admin, 'a"b;c\nd')
        assert len(row) == 15 and row[2] == 'a"b;c\nd'

    async def test_a_negative_looking_number_is_not_guarded(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day, 12)
        assert parse((await export(admin, period)).text)[1][6] == "12,00"


class TestPermission:
    async def test_a_pilot_gets_403(self, pilot):
        assert (await export(pilot, "2020-05")).status_code == 403

    async def test_anonymous_gets_401(self, anon):
        assert (await export(anon, "2020-05")).status_code == 401

    async def test_operations_may_export_it_holds_analytics(self, ops):
        assert (await export(ops, "2020-05")).status_code == 200

    async def test_a_forbidden_export_leaves_no_event(self, pilot, admin):
        period = fresh_month()[0]
        await export(pilot, period)
        events = await audit_events(admin, action="export", entity="reconciliation")
        assert not [e for e in events if e["entity_id"] == period]

    async def test_a_role_without_analytics_cannot_post_the_record_either(self, pilot):
        r = await pilot.post(f"{API}/reconciliation/export/", json={"period": "2020-05"})
        assert r.status_code == 403


class TestAudit:
    async def test_exporting_leaves_an_export_event_with_the_row_count(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        await export(admin, period)
        events = await audit_events(admin, action="export", entity="reconciliation")
        ev = next(e for e in events if e["entity_id"] == period)
        assert ev["changes"][0]["to"] == 1 and ev["actor_id"]

    async def test_the_post_records_an_export_built_elsewhere(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        r = await admin.post(f"{API}/reconciliation/export/", json={"period": period})
        assert r.status_code == 200 and r.json() == {"period": period, "rows": 1}
        events = await audit_events(admin, action="export", entity="reconciliation")
        assert any(e["entity_id"] == period for e in events)

    async def test_a_closed_period_can_still_be_exported(self, admin):
        period, day = fresh_month()
        await dated_expense(admin, await make_operation(admin), day)
        await justify_all(admin, period)
        assert (
            await admin.post(f"{API}/reconciliation/close/", json={"period": period})
        ).status_code == 200
        assert (await export(admin, period)).status_code == 200
