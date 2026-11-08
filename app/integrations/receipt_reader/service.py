"""Fachada: reintenta fallos transitorios y degrada a 'unreadable' (nunca bloquea al usuario)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .base import ReadHints, ReceiptReader
from .errors import ReceiptReaderError
from .types import ReceiptReading

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReaderRetry:
    max_retries: int = 2
    base_delay: float = 0.5
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)


class ReceiptReadingService:
    def __init__(self, reader: ReceiptReader, retry: ReaderRetry | None = None):
        self._reader = reader
        self._retry = retry or ReaderRetry()

    async def read(self, image: bytes, mime: str, hints: ReadHints) -> ReceiptReading:
        """Nunca levanta por fallos del lector: devuelve 'unreadable' y deja el log."""
        provider = getattr(self._reader, "PROVIDER", type(self._reader).__name__)
        attempt = 0
        while True:
            try:
                return await self._reader.read(image, mime, hints)
            except ReceiptReaderError as exc:
                if exc.transient and attempt < self._retry.max_retries:
                    delay = self._retry.base_delay * (2**attempt)
                    attempt += 1
                    logger.warning(
                        "Lector de comprobantes transitorio [%s], reintento %s/%s",
                        exc.code,
                        attempt,
                        self._retry.max_retries,
                    )
                    await self._retry.sleep(delay)
                    continue
                logger.warning("Lectura degradada a manual [%s]", exc.code)
                return ReceiptReading.unreadable(provider)
            except Exception as exc:  # fail closed: cualquier fallo inesperado = carga manual
                logger.error("Lector de comprobantes falló (%s)", type(exc).__name__)
                return ReceiptReading.unreadable(provider)

    @staticmethod
    def confidence_gate(reading: ReceiptReading) -> list[str]:
        """Claves que exigen revisión obligatoria: baja confianza o sin valor leído."""
        if reading.status != "extracted":
            return []
        return [f.key for f in reading.fields if f.confidence != "high" or f.value is None]
