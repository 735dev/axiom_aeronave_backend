from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.utils.reconciliation import (
    Acknowledgement,
    Expense,
    Receipt,
    compute,
    period_bounds,
    period_of,
    same_amount,
)


def expense(**over):
    base = dict(
        ticket_id="e1",
        code="G-1",
        title="Gasto",
        concept="Combustible",
        operation_id="o1",
        operation_code="YV-1",
        accounting_date=date(2026, 3, 10),
        status_name="Registrado",
        committed=True,
        amount=Decimal("100"),
        currency="USD",
        amount_base=Decimal("100"),
        base_currency="USD",
        supports=1,
        has_reading=True,
        receipts=[Receipt("f1", "r.png", Decimal("100"), "USD")],
    )
    base.update(over)
    return Expense(**base)


class TestPeriods:
    def test_period_of(self):
        assert period_of(date(2026, 3, 31)) == "2026-03"

    def test_bounds_of_february_in_a_leap_year(self):
        assert period_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))

    def test_bounds_of_december(self):
        assert period_bounds("2026-12")[1] == date(2026, 12, 31)


class TestSameAmount:
    @pytest.mark.parametrize("a,b,ok", [("100", "100.01", True), ("100", "100.02", False)])
    def test_one_cent_tolerance(self, a, b, ok):
        assert same_amount(Decimal(a), Decimal(b)) is ok


class TestCompute:
    def test_reconciled_has_no_differences(self):
        rep = compute("2026-03", [expense()], {}, "USD")
        assert rep.differences == [] and rep.rows[0].state.value == "reconciled"

    def test_keys_are_stable_between_runs(self):
        e = [expense(supports=0, has_reading=False, receipts=[])]
        a = compute("2026-03", e, {}, "USD").differences[0].key
        b = compute("2026-03", e, {}, "USD").differences[0].key
        assert a == b

    def test_an_acknowledgement_is_attached_by_key(self):
        e = [expense(supports=0, has_reading=False, receipts=[])]
        key = compute("2026-03", e, {}, "USD").differences[0].key
        ack = Acknowledgement("nota", "u1", "Ana", "Rol", datetime.now(timezone.utc))
        rep = compute("2026-03", e, {key: ack}, "USD")
        assert rep.differences[0].acknowledgement.note == "nota"
        assert rep.open_differences == 0
