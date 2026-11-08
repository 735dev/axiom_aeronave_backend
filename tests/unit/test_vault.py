import pytest

from app.integrations.blackbox import BlackboxError
from tests.unit.conftest import PDF

OP = "YV2323-004-26"


async def archive(
    vault, ticket="expense", field="support", name="f.pdf", key="k", index=None, op=OP
):
    return await vault.archive_attachment(op, ticket, field, name, PDF, key, index)


async def test_carpeta_por_operacion(vault, fake):
    doc = await archive(vault)
    assert doc.external_client_id == OP
    assert ("empresa-a", f"Maestro - {OP}") in fake.folders


async def test_dos_operaciones_dos_carpetas(vault, fake):
    await archive(vault, op="OP-1", key="a")
    await archive(vault, op="OP-2", key="b")
    assert {n for (_, n) in fake.folders} == {"Maestro - OP-1", "Maestro - OP-2"}


@pytest.mark.parametrize(
    "ticket,field,expected",
    [
        ("expense", "support", "comprobante"),
        ("technical_report", "photo", "evidencia_tecnica"),
        ("operation", "manifest", "manifiesto"),
        ("inac_permit", "approval", "permiso_inac"),
        ("hangar_incident", "photo", "evidencia_hangar"),
        ("expense", "passport", "pasaporte"),
        ("otro", None, "comprobante"),
    ],
)
async def test_document_type_llega_a_blackbox(vault, fake, ticket, field, expected):
    await archive(vault, ticket=ticket, field=field)
    assert fake.docs[-1].document_type == expected


async def test_misma_clave_no_duplica(vault, fake):
    a = await archive(vault, key="tk-9", index=1)
    b = await archive(vault, key="tk-9", index=1)
    assert b.already_stored and a.id == b.id and len(fake.docs) == 1


async def test_indice_distinto_guarda_ambos(vault, fake):
    await archive(vault, key="tk-9", index=0)
    await archive(vault, key="tk-9", index=1)
    assert len(fake.docs) == 2


async def test_download_links(vault):
    await archive(vault, key="a", name="uno.pdf")
    await archive(vault, key="b", name="dos.png")
    links = await vault.download_links(OP)
    assert [l.filename for l in links] == ["dos.png", "uno.pdf"]
    assert all(l.url.startswith("https://") for l in links)


async def test_download_links_operacion_sin_documentos(vault):
    assert await vault.download_links("OP-VACIA") == []


async def test_download_links_no_mezcla_operaciones(vault):
    await archive(vault, op="OP-1", key="a")
    await archive(vault, op="OP-2", key="b")
    assert len(await vault.download_links("OP-1")) == 1


async def test_errores_permanentes_se_propagan(vault, fake):
    with pytest.raises(BlackboxError) as exc:
        await archive(vault, name="malo.exe")
    assert exc.value.codigo == "extension"
    assert fake.docs == []


async def test_error_transitorio_agotado_se_propaga(vault, fake):
    fake.fail_next(20, 503)
    with pytest.raises(BlackboxError) as exc:
        await archive(vault)
    assert exc.value.transient


async def test_operacion_invalida_falla_antes_de_la_red(vault, fake):
    with pytest.raises(ValueError):
        await archive(vault, op="con espacio")
    with pytest.raises(ValueError):
        await vault.download_links("../x")
    assert fake.requests == []
