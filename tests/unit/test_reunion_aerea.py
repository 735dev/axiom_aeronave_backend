"""Meeting of 2026-11-16: conditional fields, euro math, and payload migration."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.exceptions.tickets import TicketError
from app.scripts.apply_reunion_aerea import migrate_operation_payload
from app.utils.money import eur_per_usd, resolve_money
from app.utils.ticket_payload import validate_payload
from app.utils.type_schema import normalize_fields


def _field(**values):
    row = {
        "key": "note",
        "label": "Nota",
        "type": "text",
        "required": False,
        "width": "half",
    }
    row.update(values)
    return row


def test_when_depends_on_a_checkbox():
    fields = normalize_fields(
        [
            _field(key="catering", label="Catering", type="checkbox"),
            _field(
                key="catering_detail",
                label="Detalle",
                type="textarea",
                depends_on="when:catering",
            ),
        ]
    )
    assert fields[1]["depends_on"] == "when:catering"


def test_a_select_cannot_depend_on_a_checkbox_without_when():
    with pytest.raises(TicketError):
        normalize_fields(
            [
                _field(key="catering", label="Catering", type="checkbox"),
                _field(
                    key="detail",
                    label="Detalle",
                    type="select",
                    options=[{"value": "a", "label": "A"}],
                    depends_on="catering",
                ),
            ]
        )


def test_eur_amount_becomes_dollars_from_the_two_bolivar_quotes():
    per_base = eur_per_usd(Decimal("100"), Decimal("110"))
    result = resolve_money(
        amount_value=1000,
        currency="EUR",
        currency_name="Euro",
        manual_rate=per_base,
        parent_rate=None,
    )
    assert result.error is None
    assert result.money.amount_base == Decimal("1100.00")


def test_a_missing_rate_is_refused_in_spanish():
    result = resolve_money(
        amount_value=10,
        currency="VES",
        currency_name="Bolívar",
        manual_rate=None,
        parent_rate=None,
    )
    assert result.error == (
        "Falta la tasa del día para convertir VES a USD. No se inventa una tasa."
    )


def test_a_hidden_when_field_is_not_required():
    detail = SimpleNamespace(
        key="catering_detail",
        label="Detalle",
        type="textarea",
        required=True,
        read_only=False,
        edit_permission=None,
        depends_on="when:catering",
        catalog=None,
        options=None,
    )
    catering = SimpleNamespace(
        key="catering",
        label="Catering",
        type="checkbox",
        required=False,
        read_only=False,
        edit_permission=None,
        depends_on=None,
        catalog=None,
        options=None,
    )
    hidden = validate_payload(
        [catering, detail],
        {"catering": False},
        existing=None,
        creating=True,
        held=frozenset(),
        catalogs={},
    )
    assert "catering_detail" not in hidden.errors
    visible = validate_payload(
        [catering, detail],
        {"catering": True},
        existing=None,
        creating=True,
        held=frozenset(),
        catalogs={},
    )
    assert visible.errors.get("catering_detail")


def test_payload_migration_wraps_a_condition_and_keeps_overnight():
    moved = migrate_operation_payload({"service_condition": "round_trip", "overnight": True})
    assert moved["service_condition"] == ["round_trip", "overnight"]
    assert moved["overnight"] is True
    kept = migrate_operation_payload({"service_condition": "libre", "overnight": False})
    assert kept["service_condition"] == "libre"
    only = migrate_operation_payload({"overnight": True})
    assert only["service_condition"] == ["overnight"] and only["overnight"] is True
