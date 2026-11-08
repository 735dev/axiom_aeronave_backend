"""Pre-validación local: replica los límites de Blackbox sin enviar nada.

Mismo orden que el servidor (idempotencia, extensión, vacío, tamaño) y los mismos `codigo`.
"""

from __future__ import annotations

from pathlib import PurePath

from .errors import (
    EXTENSION,
    IDEMPOTENCIA,
    TAMANO,
    VACIO,
    VALIDACION,
    BlackboxValidationError,
)

ALLOWED_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"})
MAX_BYTES = 20 * 1024 * 1024
MAX_IDEMPOTENCY_KEY_LEN = 200
MAX_EXTERNAL_CLIENT_ID_LEN = 100
MAX_DOCUMENT_TYPE_LEN = 100


def _has_control_chars(value: str) -> bool:
    # 127 (DEL) también: el servidor no lo rechaza, pero nunca es legítimo en un header.
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


def validate_idempotency_key(key: str) -> None:
    # ASCII imprimible: los headers HTTP no garantizan otra cosa de punta a punta.
    if (
        not isinstance(key, str)
        or not key.strip()
        or len(key) > MAX_IDEMPOTENCY_KEY_LEN
        or _has_control_chars(key)
        or not key.isascii()
    ):
        raise BlackboxValidationError(400, IDEMPOTENCIA, "Idempotency-Key ausente o inválida")


def validate_extension(filename: str) -> str:
    """Devuelve la extensión en minúsculas. Cuenta solo el último sufijo, como el servidor."""
    ext = PurePath(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise BlackboxValidationError(400, EXTENSION, "Tipo de archivo no permitido")
    return ext


def validate_content(content: bytes) -> None:
    if not content:
        raise BlackboxValidationError(400, VACIO, "El archivo está vacío")
    if len(content) > MAX_BYTES:
        raise BlackboxValidationError(413, TAMANO, "El archivo supera el máximo de 20 MB")


def validate_external_client_id(external_client_id: str) -> None:
    # El servidor responde 422 de FastAPI (sin `codigo`); usamos un código propio.
    if (
        not isinstance(external_client_id, str)
        or not 1 <= len(external_client_id) <= MAX_EXTERNAL_CLIENT_ID_LEN
        or _has_control_chars(external_client_id)
    ):
        raise BlackboxValidationError(
            422, VALIDACION, "external_client_id debe tener entre 1 y 100 caracteres"
        )


def validate_document_type(document_type: str) -> None:
    if (
        not isinstance(document_type, str)
        or not document_type.strip()
        or len(document_type) > MAX_DOCUMENT_TYPE_LEN
        or _has_control_chars(document_type)
    ):
        raise BlackboxValidationError(
            422, VALIDACION, "document_type debe tener entre 1 y 100 caracteres"
        )


def validate_upload(
    content: bytes,
    filename: str,
    external_client_id: str,
    document_type: str,
    idempotency_key: str,
) -> str:
    """Valida un envío completo; devuelve la extensión."""
    validate_external_client_id(external_client_id)
    validate_document_type(document_type)
    validate_idempotency_key(idempotency_key)
    ext = validate_extension(filename)
    validate_content(content)
    return ext
