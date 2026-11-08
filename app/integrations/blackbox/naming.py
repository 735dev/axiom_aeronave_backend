"""Decide las entradas de Blackbox a partir de conceptos de Aeronaves (funciones puras)."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .rules import MAX_EXTERNAL_CLIENT_ID_LEN, MAX_IDEMPOTENCY_KEY_LEN

_SAFE_RE = re.compile(r"[A-Za-z0-9._-]+")
DEFAULT_DOCUMENT_TYPE = "comprobante"

# (tipo de ticket, campo) -> document_type
_DOCUMENT_TYPES: dict[tuple[str, str], str] = {
    ("expense", "support"): "comprobante",
    ("technical_report", "photo"): "evidencia_tecnica",
    ("operation", "manifest"): "manifiesto",
    ("inac_permit", "approval"): "permiso_inac",
    ("hangar_incident", "photo"): "evidencia_hangar",
}


def external_client_id_for_operation(code: str) -> str:
    """El código de operación (ej. `YV2323-004-26`) es el cliente externo: una carpeta por operación."""
    if (
        not isinstance(code, str)
        or not 1 <= len(code) <= MAX_EXTERNAL_CLIENT_ID_LEN
        or not _SAFE_RE.fullmatch(code)
    ):
        raise ValueError("Código de operación inválido para Blackbox")
    return code


def document_type_for(ticket_type_code: str, field_key: str | None) -> str:
    # El pasaporte manda sobre el tipo de ticket: es el mismo documento sin importar dónde se suba.
    field = (field_key or "").strip().lower()
    if field == "passport":
        return "pasaporte"
    ticket = (ticket_type_code or "").strip().lower()
    return _DOCUMENT_TYPES.get((ticket, field), DEFAULT_DOCUMENT_TYPE)


def idempotency_key_for(client_key: str, index: int | None = None) -> str:
    """Clave determinista: mismo adjunto lógico, misma clave, aunque se reintente o se reenvíe."""
    if not isinstance(client_key, str) or not client_key:
        raise ValueError("client_key requerido")
    if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
        raise ValueError("index debe ser un entero >= 0")
    suffix = "" if index is None else f":a{index}"
    key = f"aeronaves:{client_key}{suffix}"
    # Sin ':' en el charset seguro, una clave legible nunca choca con una hasheada.
    if len(key) <= MAX_IDEMPOTENCY_KEY_LEN and _SAFE_RE.fullmatch(client_key):
        return key
    digest = hashlib.sha256(f"{client_key}\x00{index}".encode("utf-8")).hexdigest()
    return f"aeronaves:h:{digest}"


_OPERATION_RE = re.compile(r"^([A-Z0-9]+)-(\d{3,})-(\d{2})$")


def folder_id_for(operation_code: str | None, fallback_year: int) -> str:
    """Carpeta `AAAA-MATRICULA-NNN`: Blackbox ordena por nombre, así que el orden sale del nombre.

    Con el código tal cual (`YV2323-004-26`) el año quedaría al final y 2026 y 2027 se mezclarían.
    Lo que no cuelga de una operación va a `AAAA-SIN-OPERACION`.
    """
    match = _OPERATION_RE.fullmatch(operation_code or "")
    if match:
        matricula, correlativo, yy = match.groups()
        return f"20{yy}-{matricula}-{correlativo}"
    if (
        isinstance(fallback_year, bool)
        or not isinstance(fallback_year, int)
        or not 2000 <= fallback_year <= 2999
    ):
        raise ValueError("fallback_year inválido")
    return f"{fallback_year}-SIN-OPERACION"


def archive_file_name(
    ticket_code: str, document_type: str, sequence: int, original_name: str
) -> str:
    """`TK-0002_comprobante_01_recibo.jpg`: agrupa por ticket y tipo, conserva el orden de carga."""
    if not _SAFE_RE.fullmatch(ticket_code or ""):
        raise ValueError("ticket_code inválido")
    if not _SAFE_RE.fullmatch(document_type or ""):
        raise ValueError("document_type inválido")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError("sequence debe ser un entero >= 1")
    base = (original_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    ascii_stem = unicodedata.normalize("NFD", stem).encode("ascii", "ignore").decode()
    clean = re.sub(r"[^A-Za-z0-9]+", "-", ascii_stem).strip("-")[:40]
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())
    tail = f"_{clean}" if clean else ""
    return f"{ticket_code}_{document_type}_{sequence:02d}{tail}{'.' + ext if ext else ''}"
