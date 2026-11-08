import logging

import httpx
import pytest

from app.integrations.blackbox import BlackboxClient, BlackboxError
from tests.unit.conftest import BASE_URL, PDF, TOKEN

SECRET = "bb_maestro_ULTRASECRETO123"


@pytest.mark.parametrize(
    "url",
    [
        "http://blackboxve.store",
        "http://10.0.0.5:8003",
        "http://localhostevil.com",
        "ftp://localhost",
        "blackboxve.store",
        "",
        "https://",
        "https://user:pw@blackbox.fake.invalid",
    ],
)
def test_base_url_insegura_rechazada(url):
    with pytest.raises(ValueError) as exc:
        BlackboxClient(url, SECRET)
    assert SECRET not in str(exc.value) and "pw" not in str(exc.value)


@pytest.mark.parametrize(
    "url", ["https://blackbox.fake.invalid", "http://localhost:8003", "http://127.0.0.1:8003/"]
)
def test_base_url_aceptada(url):
    BlackboxClient(url, SECRET)


@pytest.mark.parametrize("token", ["", "tiene espacio", "con\nsalto", "ñ"])
def test_token_invalido_rechazado_sin_eco(token):
    with pytest.raises(ValueError) as exc:
        BlackboxClient(BASE_URL, token)
    assert token not in str(exc.value) or token == ""


def test_timeout_invalido():
    with pytest.raises(ValueError):
        BlackboxClient(BASE_URL, SECRET, timeout=0)


def test_repr_del_cliente_oculta_token():
    c = BlackboxClient(BASE_URL, SECRET)
    assert SECRET not in repr(c) and SECRET not in str(c) and "bb_maestro" not in repr(c)


async def test_token_no_aparece_en_errores_ni_logs(fake, caplog):
    """El servidor que hace eco del token no debe filtrarlo en excepción ni en logs."""

    def handler(request):
        echo = request.headers["authorization"]
        return httpx.Response(401, json={"detail": f"rechazado {echo}", "codigo": "credencial"})

    caplog.set_level(logging.DEBUG)
    c = BlackboxClient(BASE_URL, SECRET, transport=httpx.MockTransport(handler))
    with pytest.raises(BlackboxError) as exc:
        await c.upload_document(PDF, "a.pdf", "OP", "t", "k")
    await c.aclose()
    err = exc.value
    for text in (str(err), repr(err), err.detail, repr(err.args)):
        assert "ULTRASECRETO" not in text
    assert "ULTRASECRETO" not in caplog.text
    assert "[redactado]" in err.detail
    assert caplog.text  # el log existe: la aserción anterior no es vacía


async def test_token_no_aparece_en_errores_de_red_ni_logs(fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.add_token(SECRET)
    fake.timeout_next()
    c = BlackboxClient(BASE_URL, SECRET, transport=fake.transport())
    with pytest.raises(BlackboxError) as exc:
        await c.list_documents("OP")
    await c.aclose()
    assert "ULTRASECRETO" not in f"{exc.value!s}{exc.value!r}{caplog.text}"
    assert "timeout" in caplog.text


async def test_error_de_red_no_encadena_la_peticion(fake):
    fake.add_token(SECRET)
    fake.timeout_next()
    c = BlackboxClient(BASE_URL, SECRET, transport=fake.transport())
    with pytest.raises(BlackboxError) as exc:
        await c.list_documents("OP")
    await c.aclose()
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


async def test_token_viaja_solo_en_authorization(fake):
    c = BlackboxClient(BASE_URL, TOKEN, transport=fake.transport())
    await c.upload_document(PDF, "a.pdf", "OP", "t", "k")
    await c.aclose()
    req = fake.requests[0]
    assert TOKEN.encode() not in req.content and TOKEN not in str(req.url)
