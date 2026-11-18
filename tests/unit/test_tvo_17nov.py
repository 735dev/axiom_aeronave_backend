"""El papel del 17-11 entra en el seed y en el parche del formulario."""

import json

from app.fixtures.tvo_17nov import AIRPORTS, FBO, FLEET, patch_operation_fields, sheet_extra
from app.utils.seed_loader import load_seeds


def test_the_paper_fleet_stays_on_rav_91():
    sheets = {item["code"]: json.loads(sheet_extra(item)) for item in FLEET}
    assert set(sheets) == {"YV2822", "YV3143"}
    assert sheets["YV2822"]["regime"] == "rav_91"
    assert sheets["YV2822"]["serial"] == "FA122"
    assert sheets["YV2822"]["captain"] == "Richard Ollarves"
    assert "135" in sheets["YV2822"]["certification"]
    assert sheets["YV3143"]["regime"] == "rav_91"
    assert sheets["YV3143"]["copilot"] == "Adriano Trevisi"
    assert "precio" not in json.dumps(sheets)


def test_the_oaci_list_marks_maiquetia_and_caracas_apart():
    by_code = {item["code"]: item for item in AIRPORTS}
    assert len(by_code) == 42
    assert sum(item["extra"] == "domestic" for item in AIRPORTS) == 29
    assert sum(item["extra"] == "international" for item in AIRPORTS) == 13
    assert by_code["SVMI"]["extra"] == "domestic"
    assert by_code["SVCS"]["extra"] == "domestic"
    assert by_code["KFXE"]["name"].startswith("KFXE · Fort Lauderdale")
    assert by_code["SPZO"]["extra"] == "international"
    assert "2820" not in by_code and "3300" not in by_code


def test_the_fbo_has_a_name_and_no_price():
    assert FBO["name"] == "Caracas Flight Service, C.A."
    assert FBO["extra"] == "J-400878284"
    assert "usd" not in FBO["extra"].lower()


def test_the_seed_carries_the_paper():
    catalogs = load_seeds().catalogs
    tails = {item["code"]: item for item in catalogs["aircrafts"]}
    assert tails["YV2822"]["extra"] == "king_air_300"
    assert tails["YV3143"]["extra"] == "pa_31t"
    airports = {item["code"] for item in catalogs["airports"]}
    assert {item["code"] for item in AIRPORTS} <= airports
    assert catalogs["fbos"][0]["code"] == "caracas_flight_service"
    operation = next(item for item in load_seeds().ticket_types if item["code"] == "operation")
    keys = [field["key"] for field in operation["fields"]]
    assert keys.index("fbo_operator") == keys.index("fbo_name") + 1
    assert keys.index("fuel_notes") == keys.index("fuel_slip") + 1
    slip = next(field for field in operation["fields"] if field["key"] == "fuel_slip")
    assert slip["label"] == "Número de boleta"


def test_the_ground_form_gains_the_paper_lines_once():
    fields = [
        {"key": "fbo_name", "label": "FBO", "type": "select"},
        {"key": "tow_lines", "label": "Remolques", "type": "textarea", "help": "antes"},
        {"key": "fuel_slip", "label": "Número de vale", "type": "text"},
    ]
    once = patch_operation_fields(fields)
    twice = patch_operation_fields(once)
    assert [field["key"] for field in twice] == [
        "fbo_name",
        "fbo_operator",
        "tow_lines",
        "fuel_slip",
        "fuel_notes",
    ]
    assert twice[2]["help"].startswith("Un remolque por línea")
    assert twice[3]["label"] == "Número de boleta"
    assert once == twice
