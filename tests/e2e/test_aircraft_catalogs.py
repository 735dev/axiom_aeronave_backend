"""Marca y modelo son listas de catálogo y deben ser coherentes con la matrícula (§6.2)."""

import pytest

from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    TICKETS,
    OPERATION,
    field,
    get_ticket,
    make_operation,
    patch,
    status,
    type_body,
    type_by_code,
)

LEGACY_TYPE = "legacy_aircraft_text"
ITEMS = f"{API}/catalog-items/"
TYPES = f"{API}/ticket-types/"


async def items(api, catalog):
    r = await api.get(f"{ITEMS}?catalog={catalog}&limit=100")
    assert r.status_code == 200, r.text
    return {i["code"]: i for i in r.json()["data"]}


class TestCatalogs:
    async def test_the_two_catalogs_are_listed_with_their_items(self, admin):
        keys = {c["key"]: c for c in (await admin.get(f"{API}/catalogs/")).json()}
        assert keys["aircraft_brands"]["name"] == "Marcas de aeronave"
        assert keys["aircraft_models"]["name"] == "Modelos de aeronave"
        assert keys["aircraft_brands"]["items_count"] >= 2
        assert keys["aircraft_models"]["items_count"] >= 2

    async def test_models_carry_their_brand_and_aircrafts_their_model(self, admin):
        models, fleet = await items(admin, "aircraft_models"), await items(admin, "aircrafts")
        assert models["king_air_200"]["extra"] == "beechcraft"
        assert models["cheyenne"]["extra"] == "piper"
        assert fleet["YV2323"]["extra"] == "king_air_200"
        assert fleet["YV2528"]["extra"] == "cheyenne"
        assert fleet["YV2822"]["extra"] == "king_air_200"

    async def test_search_inside_the_new_catalogs(self, pilot):
        data = (await pilot.get(f"{ITEMS}?catalog=aircraft_models&q=cheyen")).json()["data"]
        assert [i["code"] for i in data] == ["cheyenne"]


class TestOperationFields:
    async def test_the_type_exposes_selects_filtered_by_brand(self, admin):
        fields = {f["key"]: f for f in (await type_by_code(admin, "operation"))["fields"]}
        brand, model = fields["aircraft_brand"], fields["aircraft_model"]
        assert (brand["type"], brand["catalog"], brand["read_only"]) == (
            "select",
            "aircraft_brands",
            False,
        )
        assert (model["type"], model["catalog"], model["depends_on"]) == (
            "select",
            "aircraft_models",
            "aircraft_brand",
        )
        assert brand["role"] == "aircraft_brand" and model["role"] == "aircraft_model"
        assert brand["depends_on"] is None

    async def test_visible_order_follows_the_paper_form(self, admin):
        ordered = sorted(
            (await type_by_code(admin, "operation"))["fields"], key=lambda f: f["sort_order"]
        )
        assert [f["key"] for f in ordered][:5] == [
            "date",
            "control_number",
            "aircraft",
            "aircraft_brand",
            "aircraft_model",
        ]


class TestCoherence:
    async def test_a_coherent_combination_is_stored(self, admin):
        op = await make_operation(
            admin, aircraft_brand="beechcraft", aircraft_model="king_air_200", aircraft="YV2822"
        )
        assert op["payload"]["aircraft_brand"] == "beechcraft"
        assert op["payload"]["aircraft_model"] == "king_air_200"

    async def test_all_three_absent_is_still_allowed(self, admin):
        op = await make_operation(admin)
        assert "aircraft_brand" not in op["payload"] and "aircraft_model" not in op["payload"]

    async def test_a_model_of_another_brand_is_rejected(self, admin):
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "operation",
                "payload": {
                    **OPERATION,
                    "aircraft_brand": "piper",
                    "aircraft_model": "king_air_200",
                },
            },
        )
        assert r.status_code == 422
        assert r.json()["errors"] == {"aircraft_model": "El modelo no corresponde a la marca."}

    async def test_an_aircraft_of_another_model_is_rejected(self, admin):
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "operation",
                "payload": {
                    **OPERATION,
                    "aircraft": "YV2528",
                    "aircraft_brand": "beechcraft",
                    "aircraft_model": "king_air_200",
                },
            },
        )
        assert r.status_code == 422
        assert r.json()["errors"] == {"aircraft": "La matrícula no corresponde a ese modelo."}

    @pytest.mark.parametrize(
        ("key", "value"),
        [("aircraft_brand", "airbus"), ("aircraft_model", "a320")],
    )
    async def test_an_unknown_code_is_rejected(self, admin, key, value):
        r = await admin.post(
            TICKETS, json={"type_code": "operation", "payload": {**OPERATION, key: value}}
        )
        assert r.status_code == 422 and key in r.json()["errors"]

    async def test_an_inactive_model_is_rejected(self, admin):
        model = (await items(admin, "aircraft_models"))["cheyenne"]
        off = await admin.patch(f"{ITEMS}{model['id']}/", json={"is_active": False})
        assert off.status_code == 200, off.text
        try:
            r = await admin.post(
                TICKETS,
                json={
                    "type_code": "operation",
                    "payload": {**OPERATION, "aircraft_model": "cheyenne"},
                },
            )
            assert r.status_code == 422 and "aircraft_model" in r.json()["errors"]
        finally:
            await admin.patch(f"{ITEMS}{model['id']}/", json={"is_active": True})

    async def test_an_update_is_checked_against_what_is_stored(self, admin):
        op = await make_operation(
            admin, aircraft_brand="beechcraft", aircraft_model="king_air_200", aircraft="YV2323"
        )
        wrong_brand = await patch(admin, op, payload={"aircraft_brand": "piper"})
        assert wrong_brand.status_code == 422
        assert wrong_brand.json()["errors"] == {
            "aircraft_brand": "La marca no corresponde al modelo."
        }
        wrong_model = await patch(admin, op, payload={"aircraft_model": "cheyenne"})
        assert wrong_model.status_code == 422
        assert "aircraft_model" in wrong_model.json()["errors"]
        both = await patch(
            admin, op, payload={"aircraft_brand": "piper", "aircraft_model": "cheyenne"}
        )
        assert both.status_code == 422  # coherent with each other, not with YV2323
        assert both.json()["errors"] == {
            "aircraft_model": "El modelo no corresponde a la matrícula."
        }
        other = await patch(admin, op, payload={"captain": "Pérez"})
        assert other.status_code == 200, other.text
        stored = (await get_ticket(admin, op))["payload"]
        assert stored["aircraft_model"] == "king_air_200" and stored["captain"] == "Pérez"

    async def test_other_types_are_not_affected(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            TICKETS,
            json={
                "type_code": "expense",
                "parent_id": op["id"],
                "payload": {"concept": "fuel", "amount": 10, "currency": "USD"},
            },
        )
        assert r.status_code == 201, r.text


class TestLegacyValues:
    """A ticket written when brand/model were free text stays readable and editable."""

    async def test_old_free_text_survives_the_switch_to_dropdowns(self, admin):
        # A type that holds a ticket cannot be deleted, so its code is fixed and the permission
        # catalog test lists it as a known leftover (test_modules_permissions).
        code = LEGACY_TYPE
        text_fields = [
            field("aircraft_brand", "Marca", "text"),
            field("aircraft_model", "Modelo", "text"),
            field("note", "Nota", "text"),
        ]
        r = await admin.post(
            TYPES,
            json=type_body(
                code,
                is_root=True,
                fields=text_fields,
                statuses=[
                    status("open", "Abierto", is_initial=True),
                    status("done", "Hecho", is_final=True),
                ],
            ),
        )
        assert r.status_code == 201, r.text
        created = r.json()
        made = await admin.post(
            TICKETS,
            json={
                "type_code": code,
                "payload": {"aircraft_brand": "Beech", "aircraft_model": "King Air", "note": "a"},
            },
        )
        assert made.status_code == 201, made.text
        ticket = made.json()

        select_fields = [
            field(
                "aircraft_brand",
                "Marca",
                "select",
                catalog="aircraft_brands",
                role="aircraft_brand",
            ),
            field(
                "aircraft_model",
                "Modelo",
                "select",
                catalog="aircraft_models",
                role="aircraft_model",
                depends_on="aircraft_brand",
            ),
            field("note", "Nota", "text"),
        ]
        sw = await admin.patch(f"{TYPES}{created['id']}/", json={"fields": select_fields})
        assert sw.status_code == 200, sw.text

        assert (await get_ticket(admin, ticket))["payload"]["aircraft_model"] == "King Air"
        edited = await patch(admin, ticket, payload={"note": "b"})
        assert edited.status_code == 200, edited.text
        assert edited.json()["payload"]["aircraft_model"] == "King Air"
        fixed = await patch(
            admin,
            ticket,
            payload={"aircraft_brand": "beechcraft", "aircraft_model": "king_air_200"},
        )
        assert fixed.status_code == 200, fixed.text


class TestBuilderRules:
    async def test_depends_on_must_point_to_another_select(self, admin):
        bad = [
            field("a", "A", "text"),
            field("b", "B", "select", catalog="aircraft_models", depends_on="a"),
        ]
        r = await admin.post(TYPES, json=type_body(fields=bad))
        assert r.status_code == 422

    async def test_a_field_cannot_depend_on_itself(self, admin):
        bad = [field("a", "A", "select", catalog="aircraft_models", depends_on="a")]
        r = await admin.post(TYPES, json=type_body(fields=bad))
        assert r.status_code == 422
