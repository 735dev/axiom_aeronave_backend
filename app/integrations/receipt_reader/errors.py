"""Errores de la lectura de comprobantes. Mensajes fijos: nunca eco de claves ni de cuerpos."""

from __future__ import annotations

import re

_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]*")


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redactado]")
    return _KEY_RE.sub("[redactado]", text)[:300]


class ReceiptReaderError(Exception):
    """Fallo del lector. `transient` indica si reintentar la misma petición tiene sentido."""

    def __init__(self, message: str, transient: bool = False, code: str = "lector"):
        self.transient = transient
        self.code = code
        self.message = redact(message)
        super().__init__(self.message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, transient={self.transient!r}, "
            f"message={self.message!r})"
        )
