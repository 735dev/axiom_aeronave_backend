import pytest

from app.integrations.blackbox import naming

PDF = b"%PDF-1.4 x"


def test_folder_ordena_por_anio_aeronave_correlativo():
    codes = ["YV2323-001-27", "YV2323-004-26", "YV2528-003-26", "YV2323-002-26"]
    assert sorted(naming.folder_id_for(c, 2026) for c in codes) == [
        "2026-YV2323-002",
        "2026-YV2323-004",
        "2026-YV2528-003",
        "2027-YV2323-001",
    ]


@pytest.mark.parametrize("code", [None, "", "basura", "YV2323-4-26", "yv2323-004-26"])
def test_sin_operacion_va_a_carpeta_del_anio(code):
    assert naming.folder_id_for(code, 2026) == "2026-SIN-OPERACION"


@pytest.mark.parametrize("year", [1999, 3000, "2026", True, None])
def test_anio_invalido_se_rechaza(year):
    with pytest.raises(ValueError):
        naming.folder_id_for(None, year)


def test_nombre_de_archivo():
    n = naming.archive_file_name("TK-0002", "comprobante", 3, "Recibo Gasolina ñandú.JPG")
    assert n == "TK-0002_comprobante_03_Recibo-Gasolina-nandu.jpg"


def test_nombre_no_escapa_de_la_carpeta():
    n = naming.archive_file_name("TK-1", "comprobante", 1, "../../etc/pass wd?.pdf")
    assert "/" not in n and "\\" not in n and "?" not in n and n.endswith(".pdf")


def test_nombre_sin_extension_ni_stem():
    assert naming.archive_file_name("TK-1", "comprobante", 1, "") == "TK-1_comprobante_01"


def test_nombre_largo_se_recorta():
    n = naming.archive_file_name("TK-1", "comprobante", 1, "a" * 300 + ".png")
    assert len(n) < 80 and n.endswith(".png")


@pytest.mark.parametrize("seq", [0, -1, True, "1", None])
def test_secuencia_invalida(seq):
    with pytest.raises(ValueError):
        naming.archive_file_name("TK-1", "comprobante", seq, "a.pdf")


@pytest.mark.parametrize("code", ["", "TK 1", "TK/1"])
def test_ticket_code_invalido(code):
    with pytest.raises(ValueError):
        naming.archive_file_name(code, "comprobante", 1, "a.pdf")


def test_orden_alfabetico_agrupa_por_ticket_tipo_secuencia():
    names = [
        naming.archive_file_name("TK-0002", "comprobante", 2, "b.jpg"),
        naming.archive_file_name("TK-0002", "comprobante", 1, "a.jpg"),
        naming.archive_file_name("TK-0001", "comprobante", 1, "z.jpg"),
    ]
    assert sorted(names)[0].startswith("TK-0001")
    assert sorted(names)[1:] == [names[1], names[0]]


async def test_vault_archiva_en_carpeta_ordenada(vault):
    doc = await vault.archive_ordered(
        operation_code="YV2323-004-26",
        fallback_year=2026,
        ticket_code="TK-0002",
        ticket_type_code="expense",
        field_key="support",
        original_name="r.pdf",
        sequence=1,
        content=PDF,
        client_key="k1",
    )
    assert doc.external_client_id == "2026-YV2323-004"
    assert doc.filename == "TK-0002_comprobante_01_r.pdf"
    links = await vault.folder_links("YV2323-004-26", 2026)
    assert [l.filename for l in links] == ["TK-0002_comprobante_01_r.pdf"]
    assert links[0].document_type == "comprobante"


async def test_vault_reintento_no_duplica(vault):
    args = dict(
        operation_code=None,
        fallback_year=2026,
        ticket_code="TK-9",
        ticket_type_code="hangar_incident",
        field_key="photo",
        original_name="f.png",
        sequence=1,
        content=b"\x89PNG....",
        client_key="k2",
    )
    a = await vault.archive_ordered(**args)
    b = await vault.archive_ordered(**args)
    assert a.id == b.id and b.already_stored
    assert a.external_client_id == "2026-SIN-OPERACION"
    assert len(await vault.folder_links(None, 2026)) == 1
