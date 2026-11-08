import hashlib

import pytest

from app.integrations.blackbox import rules
from app.integrations.blackbox.naming import (
    document_type_for,
    external_client_id_for_operation,
    idempotency_key_for,
)


def test_codigo_de_operacion_valido():
    assert external_client_id_for_operation("YV2323-004-26") == "YV2323-004-26"


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "ñ", "a" * 101, "x\n", "op;drop", None])
def test_codigo_de_operacion_invalido(bad):
    with pytest.raises(ValueError):
        external_client_id_for_operation(bad)


def test_codigo_de_operacion_limite_100():
    assert external_client_id_for_operation("A" * 100) == "A" * 100


@pytest.mark.parametrize(
    "ticket,field,expected",
    [
        ("expense", "support", "comprobante"),
        ("technical_report", "photo", "evidencia_tecnica"),
        ("operation", "manifest", "manifiesto"),
        ("inac_permit", "approval", "permiso_inac"),
        ("hangar_incident", "photo", "evidencia_hangar"),
    ],
)
def test_mapa_de_document_type(ticket, field, expected):
    assert document_type_for(ticket, field) == expected


def test_mismo_campo_distinto_tipo_distinto_resultado():
    assert document_type_for("technical_report", "photo") != document_type_for(
        "hangar_incident", "photo"
    )


@pytest.mark.parametrize("ticket", ["expense", "operation", "desconocido", ""])
def test_pasaporte_en_cualquier_tipo(ticket):
    assert document_type_for(ticket, "passport") == "pasaporte"


@pytest.mark.parametrize(
    "ticket,field", [("nuevo", "x"), ("expense", None), ("expense", "photo"), ("", None)]
)
def test_default_comprobante(ticket, field):
    assert document_type_for(ticket, field) == "comprobante"


def test_document_type_normaliza_mayusculas():
    assert document_type_for("Technical_Report", "PHOTO") == "evidencia_tecnica"


def test_clave_determinista():
    assert idempotency_key_for("tk-1") == idempotency_key_for("tk-1") == "aeronaves:tk-1"


def test_clave_con_indice():
    assert idempotency_key_for("tk-1", 2) == "aeronaves:tk-1:a2"


def test_indice_distinto_clave_distinta():
    keys = {idempotency_key_for("tk-1", i) for i in range(5)}
    assert len(keys) == 5


def test_indice_cero_distinto_de_sin_indice():
    assert idempotency_key_for("tk-1", 0) != idempotency_key_for("tk-1")


def test_client_key_distinto_clave_distinta():
    assert idempotency_key_for("a") != idempotency_key_for("b")


def test_sin_ambiguedad_entre_clave_con_sufijo_e_indice():
    # "x:a1" sin índice y "x" con índice 1 no pueden colisionar.
    assert idempotency_key_for("x:a1") != idempotency_key_for("x", 1)


def test_clave_larga_se_hashea_y_cumple_reglas():
    long_key = "k" * 500
    key = idempotency_key_for(long_key, 1)
    assert len(key) <= 200
    rules.validate_idempotency_key(key)
    assert key == idempotency_key_for(long_key, 1)
    assert key != idempotency_key_for(long_key, 2)
    assert key != idempotency_key_for(long_key + "x", 1)
    assert hashlib.sha256(f"{long_key}\x001".encode()).hexdigest() in key


def test_clave_con_caracteres_raros_se_hashea():
    key = idempotency_key_for("ñandú\n/con espacios")
    rules.validate_idempotency_key(key)
    assert key.startswith("aeronaves:h:")


def test_limite_exacto_200_no_hashea():
    client_key = "k" * (200 - len("aeronaves:"))
    key = idempotency_key_for(client_key)
    assert len(key) == 200 and key == f"aeronaves:{client_key}"
    assert idempotency_key_for(client_key + "k").startswith("aeronaves:h:")


@pytest.mark.parametrize("args", [("",), (None,), ("a", -1), ("a", True), ("a", "1")])
def test_clave_argumentos_invalidos(args):
    with pytest.raises(ValueError):
        idempotency_key_for(*args)
