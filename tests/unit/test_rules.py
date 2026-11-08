import pytest

from app.integrations.blackbox import BlackboxValidationError, rules

OK_KEY = "aeronaves:abc"


def codigo_of(fn, *args):
    with pytest.raises(BlackboxValidationError) as exc:
        fn(*args)
    return exc.value


def test_upload_valido_devuelve_extension():
    assert rules.validate_upload(b"x", "a.PDF", "OP-1", "comprobante", OK_KEY) == ".pdf"


@pytest.mark.parametrize("name", ["a.pdf", "a.doc", "a.docx", "a.png", "a.jpg", "a.jpeg"])
def test_extensiones_permitidas(name):
    assert rules.validate_extension(name) == name[name.index(".") :]


@pytest.mark.parametrize("name", ["a.exe", "a.txt", "a.gif", "a.svg", "a", "a.", ".pdf", ""])
def test_extensiones_rechazadas(name):
    err = codigo_of(rules.validate_extension, name)
    assert (err.status, err.codigo) == (400, "extension")


@pytest.mark.parametrize("name", ["A.PDF", "foto.JpG", "x.DocX"])
def test_extension_insensible_a_mayusculas(name):
    rules.validate_extension(name)


def test_doble_extension_cuenta_la_ultima():
    rules.validate_extension("script.exe.pdf")
    assert codigo_of(rules.validate_extension, "informe.pdf.exe").codigo == "extension"


def test_archivo_vacio():
    err = codigo_of(rules.validate_content, b"")
    assert (err.status, err.codigo) == (400, "vacio")


def test_tamano_exacto_20mb_ok():
    rules.validate_content(b"x" * rules.MAX_BYTES)


def test_tamano_20mb_mas_uno_rechazado():
    err = codigo_of(rules.validate_content, b"x" * (rules.MAX_BYTES + 1))
    assert (err.status, err.codigo) == (413, "tamano")


def test_constantes_documentadas():
    assert rules.MAX_BYTES == 20 * 1024 * 1024
    assert rules.MAX_IDEMPOTENCY_KEY_LEN == 200
    assert rules.ALLOWED_EXTENSIONS == {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}


def test_clave_200_ok_201_rechazada():
    rules.validate_idempotency_key("k" * 200)
    err = codigo_of(rules.validate_idempotency_key, "k" * 201)
    assert (err.status, err.codigo) == (400, "idempotencia")


@pytest.mark.parametrize("key", ["", "   ", "a\nb", "a\x00b", "a\tb", "a\x7fb", "ñandú"])
def test_clave_invalida(key):
    assert codigo_of(rules.validate_idempotency_key, key).codigo == "idempotencia"


def test_clave_no_string():
    assert codigo_of(rules.validate_idempotency_key, None).codigo == "idempotencia"


def test_external_client_id_limites():
    rules.validate_external_client_id("a")
    rules.validate_external_client_id("a" * 100)
    for bad in ("", "a" * 101, "a\nb"):
        assert codigo_of(rules.validate_external_client_id, bad).codigo == "validacion"


def test_document_type_limites():
    rules.validate_document_type("x" * 100)
    for bad in ("", "  ", "x" * 101):
        assert codigo_of(rules.validate_document_type, bad).codigo == "validacion"


def test_orden_de_validacion_como_el_servidor():
    # Con clave inválida Y extensión mala, el servidor responde idempotencia primero.
    err = codigo_of(rules.validate_upload, b"", "a.exe", "OP", "t", "")
    assert err.codigo == "idempotencia"
    err = codigo_of(rules.validate_upload, b"", "a.exe", "OP", "t", OK_KEY)
    assert err.codigo == "extension"
    err = codigo_of(rules.validate_upload, b"", "a.pdf", "OP", "t", OK_KEY)
    assert err.codigo == "vacio"
