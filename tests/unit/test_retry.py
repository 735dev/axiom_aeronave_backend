import pytest

from app.integrations.blackbox import AttachmentVault, BlackboxError, RetryPolicy
from tests.unit.conftest import PDF

ARGS = dict(
    operation_code="YV2323-004-26",
    ticket_type_code="expense",
    field_key="support",
    filename="factura.pdf",
    content=PDF,
    client_key="tk-1",
    index=0,
)


def idem_keys(fake):
    return [r.headers["idempotency-key"] for r in fake.requests]


async def test_transitorio_se_reintenta_con_la_misma_clave_y_termina_bien(vault, fake, sleeps):
    fake.fail_next(2, 503)
    doc = await vault.archive_attachment(**ARGS)
    assert doc.status == "stored"
    assert len(fake.requests) == 3
    assert len(set(idem_keys(fake))) == 1 and idem_keys(fake)[0] == "aeronaves:tk-1:a0"
    assert len(fake.docs_of("empresa-a")) == 1


async def test_backoff_exponencial(vault, fake, sleeps):
    fake.fail_next(3, 500)
    await vault.archive_attachment(**ARGS)
    assert sleeps == [0.5, 1.0, 2.0]


async def test_intentos_acotados(vault, fake, sleeps):
    fake.fail_next(10, 503)
    with pytest.raises(BlackboxError) as exc:
        await vault.archive_attachment(**ARGS)
    assert exc.value.status == 503
    assert len(fake.requests) == 4  # 1 intento + 3 reintentos
    assert len(sleeps) == 3


async def test_permanente_no_se_reintenta(vault, fake, sleeps):
    with pytest.raises(BlackboxError) as exc:
        await vault.archive_attachment(**{**ARGS, "filename": "virus.exe"})
    assert exc.value.codigo == "extension"
    assert fake.requests == [] and sleeps == []


async def test_permanente_del_servidor_no_se_reintenta(vault, fake, sleeps):
    fake.fail_next(1, 403)
    with pytest.raises(BlackboxError):
        await vault.archive_attachment(**ARGS)
    assert len(fake.requests) == 1 and sleeps == []


async def test_credencial_invalida_no_se_reintenta(make_client, fake, sleeps):
    async def s(d):
        sleeps.append(d)

    v = AttachmentVault(make_client("bb_maestro_MALO"), RetryPolicy(sleep=s))
    with pytest.raises(BlackboxError) as exc:
        await v.archive_attachment(**ARGS)
    assert exc.value.codigo == "credencial" and len(fake.requests) == 1


async def test_timeout_se_reintenta(vault, fake):
    fake.timeout_next(2)
    doc = await vault.archive_attachment(**ARGS)
    assert doc.status == "stored" and len(fake.requests) == 3


async def test_aplicar_y_perder_respuesta_no_duplica(vault, fake):
    fake.apply_then_lose_response_next(1)
    doc = await vault.archive_attachment(**ARGS)
    # El primer intento guardó el documento; el reintento solo lo reconoce.
    assert doc.already_stored
    assert len(fake.requests) == 2
    assert len(fake.docs_of("empresa-a")) == 1


async def test_perder_respuesta_varias_veces_sigue_sin_duplicar(vault, fake):
    fake.apply_then_lose_response_next(3)
    doc = await vault.archive_attachment(**ARGS)
    assert doc.already_stored and len(fake.docs_of("empresa-a")) == 1


async def test_sin_reintentos_configurados(client, fake):
    async def never(_):
        raise AssertionError("no debe dormir")

    v = AttachmentVault(client, RetryPolicy(max_retries=0, sleep=never))
    fake.fail_next(1, 503)
    with pytest.raises(BlackboxError):
        await v.archive_attachment(**ARGS)
    assert len(fake.requests) == 1


async def test_reintento_tambien_en_download_links(vault, fake):
    await vault.archive_attachment(**ARGS)
    fake.fail_next(1, 502)
    assert len(await vault.download_links(ARGS["operation_code"])) == 1


async def test_politica_no_traga_excepciones_ajenas():
    async def boom():
        raise KeyError("x")

    with pytest.raises(KeyError):
        await RetryPolicy().run(boom)
