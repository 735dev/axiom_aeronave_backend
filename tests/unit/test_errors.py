import pytest

from app.integrations.blackbox import (
    BlackboxCredentialError,
    BlackboxError,
    BlackboxNetworkError,
    BlackboxNotFoundError,
    BlackboxValidationError,
)
from app.integrations.blackbox.errors import from_response


@pytest.mark.parametrize(
    "status,expected",
    [(400, False), (401, False), (403, False), (404, False), (413, False), (422, False)]
    + [(408, True), (429, True), (500, True), (502, True), (503, True), (504, True)],
)
def test_transient_por_status(status, expected):
    assert BlackboxError(status, "x", "y").transient is expected


def test_error_de_red_es_transitorio():
    err = BlackboxNetworkError("timeout", "t")
    assert err.transient and err.status is None


@pytest.mark.parametrize(
    "status,cls",
    [
        (401, BlackboxCredentialError),
        (403, BlackboxCredentialError),
        (400, BlackboxValidationError),
        (413, BlackboxValidationError),
        (404, BlackboxNotFoundError),
        (500, BlackboxError),
    ],
)
def test_subclase_por_status(status, cls):
    assert type(from_response(status, b"{}")) is cls


def test_from_response_json_completo():
    err = from_response(400, b'{"detail":"malo","codigo":"extension"}')
    assert (err.status, err.codigo, err.detail) == (400, "extension", "malo")


def test_from_response_cuerpo_no_json():
    err = from_response(502, b"<html>Bad Gateway</html>")
    assert (err.codigo, err.transient) == ("http_502", True)
    assert "502" in err.detail


def test_from_response_sin_campos_y_tipos_raros():
    assert from_response(400, b"[]").codigo == "http_400"
    assert from_response(400, b'{"detail": 5, "codigo": 7}').codigo == "http_400"
    assert from_response(422, b'{"detail":[{"msg":"x"}]}').detail == "Solicitud inválida"
    assert from_response(400, b"\xff\xfe").codigo == "http_400"


def test_detail_se_acota():
    big = b'{"detail":"' + b"a" * 5000 + b'","codigo":"x"}'
    assert len(from_response(400, big).detail) <= 300


def test_token_redactado_en_str_repr_y_detail():
    body = b'{"detail":"token bb_maestro_SECRETO invalido","codigo":"credencial"}'
    err = from_response(401, body)
    for text in (str(err), repr(err), err.detail, str(err.args)):
        assert "SECRETO" not in text
    assert "[redactado]" in err.detail


def test_secreto_exacto_redactado_aunque_no_tenga_prefijo():
    err = from_response(401, b'{"detail":"hola s3cr3t-xyz","codigo":"c"}', secrets=("s3cr3t-xyz",))
    assert "s3cr3t" not in str(err)
