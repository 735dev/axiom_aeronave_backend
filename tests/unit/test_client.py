import uuid

import httpx
import pytest

from app.integrations.blackbox import (
    BlackboxClient,
    BlackboxCredentialError,
    BlackboxError,
    BlackboxNetworkError,
    BlackboxNotFoundError,
    BlackboxValidationError,
    DocumentRef,
    StoredDocument,
)
from tests.unit.conftest import BASE_URL, OTHER_TOKEN, PDF, TOKEN

CLIENT = "YV2323-004-26"


async def upload(client, key="k1", name="rif.pdf", content=PDF, doc_type="rif", cid=CLIENT):
    return await client.upload_document(content, name, cid, doc_type, key)


async def test_upload_happy_path(client, fake):
    doc = await upload(client)
    assert isinstance(doc, StoredDocument)
    assert doc.status == "stored" and not doc.already_stored
    assert (doc.filename, doc.size, doc.external_client_id) == ("rif.pdf", len(PDF), CLIENT)
    stored = fake.docs_of("empresa-a")
    assert len(stored) == 1 and stored[0].content == PDF
    assert stored[0].document_type == "rif"
    assert fake.folders[("empresa-a", f"Maestro - {CLIENT}")] == doc.folder_id


async def test_upload_envia_headers_y_campos_documentados(client, fake):
    await upload(client, key="clave-xyz")
    req = fake.requests[0]
    assert req.method == "POST" and req.url.path == "/api/v1/integrations/maestro/documents/"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["idempotency-key"] == "clave-xyz"
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="external_client_id"' in req.content and CLIENT.encode() in req.content
    assert b'name="document_type"' in req.content


async def test_replay_idempotente_devuelve_already_stored_sin_duplicar(client, fake):
    first = await upload(client, key="misma")
    second = await upload(client, key="misma")
    assert second.already_stored and second.status == "already_stored"
    assert second.id == first.id
    assert len(fake.docs_of("empresa-a")) == 1


async def test_clave_distinta_crea_otro_documento(client, fake):
    a = await upload(client, key="k-a")
    b = await upload(client, key="k-b")
    assert a.id != b.id and len(fake.docs_of("empresa-a")) == 2


async def test_idempotencia_es_por_empresa(fake, make_client):
    a = await upload(make_client(TOKEN), key="compartida")
    b = await upload(make_client(OTHER_TOKEN), key="compartida")
    assert not b.already_stored and a.id != b.id


async def test_misma_carpeta_para_la_misma_operacion(client):
    a = await upload(client, key="1")
    b = await upload(client, key="2")
    assert a.folder_id == b.folder_id


async def test_carpetas_distintas_por_operacion(client):
    a = await upload(client, key="1", cid="OP-1")
    b = await upload(client, key="2", cid="OP-2")
    assert a.folder_id != b.folder_id


async def test_list_documents(client):
    await upload(client, key="1", name="a.pdf", doc_type="rif")
    await upload(client, key="2", name="b.png", doc_type="pasaporte")
    await upload(client, key="3", name="otro.pdf", cid="OTRA-OP")
    refs = await client.list_documents(CLIENT)
    assert all(isinstance(r, DocumentRef) for r in refs)
    assert [r.filename for r in refs] == ["b.png", "a.pdf"]  # más reciente primero
    assert refs[0].document_type == "pasaporte" and refs[0].url.startswith("https://")
    assert refs[0].size == len(PDF) and refs[0].created_at


async def test_list_vacio(client):
    assert await client.list_documents("NADA") == []


async def test_list_limita_a_100(client, fake):
    for i in range(105):
        await upload(client, key=f"k{i}", name=f"d{i}.pdf")
    refs = await client.list_documents(CLIENT)
    assert len(refs) == 100
    assert refs[0].filename == "d104.pdf" and refs[-1].filename == "d5.pdf"


async def test_list_aislado_por_empresa(client, make_client):
    await upload(client)
    assert await make_client(OTHER_TOKEN).list_documents(CLIENT) == []


async def test_delete_ok_y_luego_404(client, fake):
    doc = await upload(client)
    assert await client.delete_document(doc.id) is None
    assert fake.docs_of("empresa-a") == []
    with pytest.raises(BlackboxNotFoundError) as exc:
        await client.delete_document(doc.id)
    assert exc.value.codigo == "no_encontrado" and not exc.value.transient


async def test_delete_de_otra_empresa_es_404(client, make_client, fake):
    doc = await upload(client)
    with pytest.raises(BlackboxNotFoundError):
        await make_client(OTHER_TOKEN).delete_document(doc.id)
    assert len(fake.docs_of("empresa-a")) == 1


async def test_delete_id_no_uuid_no_llega_a_la_red(client, fake):
    for bad in ("../x", "abc", "1/../../keys", ""):
        with pytest.raises(BlackboxNotFoundError):
            await client.delete_document(bad)
    assert fake.requests == []


async def test_delete_acepta_uuid_objeto(client, fake):
    doc = await upload(client)
    await client.delete_document(uuid.UUID(doc.id))
    assert fake.docs_of("empresa-a") == []


# --- mapeo de errores del servidor ---


async def test_credencial_ausente(fake):
    # Cliente con transporte que borra el header: simula petición sin Authorization.
    def handler(request):
        request.headers.pop("authorization")
        return fake.handler(request)

    c = BlackboxClient(BASE_URL, TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(BlackboxCredentialError) as exc:
        await upload(c)
    await c.aclose()
    assert (exc.value.status, exc.value.codigo) == (401, "credencial_ausente")


async def test_credencial_invalida(make_client):
    with pytest.raises(BlackboxCredentialError) as exc:
        await upload(make_client("bb_maestro_REVOCADO"))
    assert (exc.value.status, exc.value.codigo, exc.value.transient) == (401, "credencial", False)


async def test_sin_permiso(fake, make_client):
    fake.add_token("bb_maestro_SOLOLECTURA", permissions=())
    with pytest.raises(BlackboxCredentialError) as exc:
        await upload(make_client("bb_maestro_SOLOLECTURA"))
    assert (exc.value.status, exc.value.codigo) == (403, "sin_permiso")


@pytest.mark.parametrize(
    "kwargs,status,codigo",
    [
        ({"name": "a.exe"}, 400, "extension"),
        ({"content": b""}, 400, "vacio"),
        ({"key": ""}, 400, "idempotencia"),
        ({"key": "k" * 201}, 400, "idempotencia"),
        ({"content": b"x" * (20 * 1024 * 1024 + 1)}, 413, "tamano"),
    ],
)
async def test_validacion_local_es_igual_que_la_del_servidor(client, fake, kwargs, status, codigo):
    with pytest.raises(BlackboxValidationError) as exc:
        await upload(client, **kwargs)
    assert (exc.value.status, exc.value.codigo) == (status, codigo)
    assert fake.requests == []  # no se envió nada


@pytest.mark.parametrize(
    "kwargs,status,codigo",
    [
        ({"name": "a.exe"}, 400, "extension"),
        ({"content": b""}, 400, "vacio"),
        ({"key": "a\x01b"}, 400, "idempotencia"),
    ],
)
async def test_codigos_del_servidor_se_mapean(fake, kwargs, status, codigo):
    """Salta la pre-validación local para comprobar el mapeo de la respuesta real."""
    req = dict(
        content=kwargs.get("content", PDF),
        filename=kwargs.get("name", "a.pdf"),
        external_client_id=CLIENT,
        document_type="t",
        idempotency_key=kwargs.get("key", "k"),
    )
    transport = fake.transport()
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        response = await http.post(
            "/api/v1/integrations/maestro/documents/",
            headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": req["idempotency_key"]},
            files={"file": (req["filename"], req["content"])},
            data={"external_client_id": CLIENT, "document_type": "t"},
        )
    from app.integrations.blackbox.errors import from_response

    err = from_response(response.status_code, response.content)
    assert (err.status, err.codigo) == (status, codigo)
    assert isinstance(err, BlackboxValidationError)


async def test_cuerpo_de_error_no_json(fake):
    transport = httpx.MockTransport(lambda r: httpx.Response(502, content=b"<html>bad</html>"))
    c = BlackboxClient(BASE_URL, TOKEN, transport=transport)
    with pytest.raises(BlackboxError) as exc:
        await upload(c)
    await c.aclose()
    assert exc.value.status == 502 and exc.value.codigo == "http_502" and exc.value.transient


async def test_404_de_ruta_no_publicada_sin_codigo(fake):
    # Doc: DELETE aún no está desplegado en producción -> 404 genérico de FastAPI.
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"detail": "Not Found"}))
    c = BlackboxClient(BASE_URL, TOKEN, transport=transport)
    with pytest.raises(BlackboxNotFoundError) as exc:
        await c.delete_document(str(uuid.uuid4()))
    await c.aclose()
    assert exc.value.codigo == "http_404"


async def test_respuesta_2xx_ilegible_es_error_permanente():
    transport = httpx.MockTransport(lambda r: httpx.Response(201, content=b"no json"))
    c = BlackboxClient(BASE_URL, TOKEN, transport=transport)
    with pytest.raises(BlackboxError) as exc:
        await upload(c)
    await c.aclose()
    assert exc.value.codigo == "respuesta_invalida" and not exc.value.transient


async def test_respuesta_2xx_incompleta():
    transport = httpx.MockTransport(lambda r: httpx.Response(201, json={"data": {"id": "x"}}))
    c = BlackboxClient(BASE_URL, TOKEN, transport=transport)
    with pytest.raises(BlackboxError) as exc:
        await upload(c)
    await c.aclose()
    assert exc.value.codigo == "respuesta_invalida"


async def test_redirect_no_se_sigue_y_es_error():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://atacante.invalid/robo"})

    c = BlackboxClient(BASE_URL, TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(BlackboxError) as exc:
        await upload(c)
    await c.aclose()
    assert len(seen) == 1 and "atacante" not in seen[0]
    assert exc.value.status == 302 and not exc.value.transient


# --- errores transitorios vs permanentes ---


async def test_timeout_es_transitorio(client, fake):
    fake.timeout_next()
    with pytest.raises(BlackboxNetworkError) as exc:
        await upload(client)
    assert exc.value.transient and exc.value.codigo == "timeout" and exc.value.status is None


async def test_error_de_conexion_es_transitorio(client, fake):
    fake.connect_error_next()
    with pytest.raises(BlackboxNetworkError) as exc:
        await upload(client)
    assert exc.value.transient and exc.value.codigo == "red"


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429, 408])
async def test_5xx_y_similares_son_transitorios(client, fake, status):
    fake.fail_next(1, status)
    with pytest.raises(BlackboxError) as exc:
        await upload(client)
    assert exc.value.transient and exc.value.status == status


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
async def test_4xx_son_permanentes(client, fake, status):
    fake.fail_next(1, status)
    with pytest.raises(BlackboxError) as exc:
        await upload(client)
    assert not exc.value.transient


async def test_timeout_explicito_en_cada_llamada(fake):
    c = BlackboxClient(BASE_URL, TOKEN, timeout=3.5, transport=fake.transport())
    seen = []
    original = c._http.request

    async def spy(*a, **kw):
        seen.append(kw["timeout"])
        return await original(*a, **kw)

    c._http.request = spy
    doc = await upload(c)
    await c.list_documents(CLIENT)
    await c.delete_document(doc.id)
    await c.aclose()
    assert len(seen) == 3 and all(t == httpx.Timeout(3.5) for t in seen)


async def test_cliente_usa_follow_redirects_false(client):
    assert client._http.follow_redirects is False


async def test_context_manager(fake):
    async with BlackboxClient(BASE_URL, TOKEN, transport=fake.transport()) as c:
        assert await c.list_documents("X") == []
    assert c._http.is_closed
