"""The tail fills brand and model. The regime follows the sheet, not a guess."""

from app.utils.aircraft_link import (
    MODEL_INACTIVE,
    REGIME_MISMATCH,
    align_aircraft_link,
    sheet_regime,
)

FLEET = {"YV2822": "king_air_300", "YV3143": "pa_31t", "YV0000": None}
MODELS = {"king_air_300": "beechcraft", "pa_31t": "piper"}
SHEETS = {
    "YV2822": '{"regime":"rav_91","certification":"RAV 91, en proceso para RAV 135"}',
    "YV3143": '{"regime":"rav_91"}',
}


def align(payload: dict):
    data = dict(payload)
    error = align_aircraft_link(
        data,
        aircraft_key="aircraft",
        brand_key="aircraft_brand",
        model_key="aircraft_model",
        regime_key="regime",
        fleet=FLEET,
        models=MODELS,
        sheets=SHEETS,
    )
    return data, error


def test_the_tail_fills_brand_and_model_and_an_empty_regime():
    data, error = align({"aircraft": "YV2822"})
    assert error is None
    assert data["aircraft_brand"] == "beechcraft"
    assert data["aircraft_model"] == "king_air_300"
    assert data["regime"] == "rav_91"


def test_a_handwritten_brand_is_replaced_by_the_tail():
    data, error = align(
        {"aircraft": "YV3143", "aircraft_brand": "beechcraft", "aircraft_model": "king_air_300"}
    )
    assert error is None
    assert data["aircraft_brand"] == "piper"
    assert data["aircraft_model"] == "pa_31t"


def test_certification_text_does_not_enable_rav_135():
    assert sheet_regime(SHEETS["YV2822"]) == "rav_91"
    data, error = align({"aircraft": "YV2822", "regime": "rav_135"})
    assert error == ("regime", REGIME_MISMATCH)
    assert "aircraft_brand" not in data


def test_an_inactive_model_is_refused():
    payload = {"aircraft": "YV2822"}
    error = align_aircraft_link(
        payload,
        aircraft_key="aircraft",
        brand_key="aircraft_brand",
        model_key="aircraft_model",
        regime_key="regime",
        fleet=FLEET,
        models={},
        sheets=SHEETS,
    )
    assert error == ("aircraft", MODEL_INACTIVE)
    assert "aircraft_brand" not in payload


def test_a_tail_without_a_model_link_does_not_invent_one():
    data, error = align({"aircraft": "YV0000", "aircraft_brand": "piper"})
    assert error is None
    assert "aircraft_brand" not in data
    assert "aircraft_model" not in data
