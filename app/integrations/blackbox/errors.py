"""Errores de la integración con Blackbox.

Ningún mensaje ni repr puede contener el token: `detail` viene del servidor (o de nuestras
reglas) y se redacta antes de guardarse.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

# Tokens Maestro: nunca deben sobrevivir en un mensaje, aunque el servidor los eco.
_TOKEN_RE = re.compile(r"bb_maestro_[A-Za-z0-9_\-]*")
_MAX_DETAIL = 300

# Códigos documentados por Blackbox.
CREDENCIAL_AUSENTE = "credencial_ausente"
CREDENCIAL = "credencial"
SIN_PERMISO = "sin_permiso"
EXTENSION = "extension"
VACIO = "vacio"
IDEMPOTENCIA = "idempotencia"
TAMANO = "tamano"
NO_ENCONTRADO = "no_encontrado"

# Códigos propios (el servidor no los emite).
TIMEOUT = "timeout"
RED = "red"
RESPUESTA_INVALIDA = "respuesta_invalida"
VALIDACION = "validacion"


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Quita tokens del texto y lo acota; el detalle del servidor no es de fiar."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redactado]")
    text = _TOKEN_RE.sub("[redactado]", text)
    return text[:_MAX_DETAIL]


class BlackboxError(Exception):
    """Error de Blackbox. `status` es None cuando no hubo respuesta HTTP (red/timeout)."""

    def __init__(self, status: int | None, codigo: str, detail: str = ""):
        self.status = status
        self.codigo = codigo
        self.detail = redact(detail or "")
        super().__init__(str(self))

    @property
    def transient(self) -> bool:
        """True si reintentar con la misma clave tiene sentido."""
        if self.status is None:
            return True
        return self.status in (408, 429) or self.status >= 500

    def __str__(self) -> str:
        return f"Blackbox {self.status if self.status is not None else 'sin respuesta'} [{self.codigo}]: {self.detail}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status={self.status!r}, codigo={self.codigo!r}, "
            f"detail={self.detail!r})"
        )


class BlackboxCredentialError(BlackboxError):
    """401/403: token ausente, inválido, revocado o sin permiso. Reintentar no ayuda."""


class BlackboxValidationError(BlackboxError):
    """400/413/422: el envío no es válido. Reintentar el mismo envío falla igual."""


class BlackboxNotFoundError(BlackboxError):
    """404."""


class BlackboxNetworkError(BlackboxError):
    """Sin respuesta HTTP: timeout o fallo de conexión. Siempre transitorio."""

    def __init__(self, codigo: str = RED, detail: str = ""):
        super().__init__(None, codigo, detail)


def _class_for(status: int) -> type[BlackboxError]:
    if status in (401, 403):
        return BlackboxCredentialError
    if status in (400, 413, 422):
        return BlackboxValidationError
    if status == 404:
        return BlackboxNotFoundError
    return BlackboxError


def from_response(status: int, content: bytes, secrets: Iterable[str] = ()) -> BlackboxError:
    """Construye el error desde una respuesta no-2xx, tolerando cuerpos que no son `{detail,codigo}`."""
    detail, codigo = "", ""
    try:
        body = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        body = None
    if isinstance(body, dict):
        raw_detail = body.get("detail")
        if isinstance(raw_detail, str):
            detail = raw_detail
        elif raw_detail is not None:
            # FastAPI devuelve listas en 422; no volcamos estructuras internas.
            detail = "Solicitud inválida"
        raw_codigo = body.get("codigo")
        if isinstance(raw_codigo, str):
            codigo = raw_codigo
    if not codigo:
        codigo = f"http_{status}"
    if not detail:
        detail = f"Error HTTP {status}"
    return _class_for(status)(status, codigo, redact(detail, secrets))
