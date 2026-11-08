"""Fachada de Aeronaves: archiva adjuntos en Blackbox con las reglas de naming y reintentos."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

from . import naming
from .client import BlackboxClient, DocumentRef, StoredDocument
from .errors import BlackboxError

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Reintenta solo errores transitorios; la clave de idempotencia no cambia entre intentos."""

    max_retries: int = 3
    base_delay: float = 0.5
    # Inyectable para que los tests no esperen.
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)

    async def run(self, call: Callable[[], Awaitable[T]]) -> T:
        attempt = 0
        while True:
            try:
                return await call()
            except BlackboxError as exc:
                if not exc.transient or attempt >= self.max_retries:
                    raise
                delay = self.base_delay * (2**attempt)
                attempt += 1
                logger.warning(
                    "Blackbox transitorio [%s], reintento %s/%s",
                    exc.codigo,
                    attempt,
                    self.max_retries,
                )
                await self.sleep(delay)


class AttachmentVault:
    def __init__(self, client: BlackboxClient, retry_policy: RetryPolicy | None = None):
        self._client = client
        self._retry = retry_policy or RetryPolicy()

    async def archive_attachment(
        self,
        operation_code: str,
        ticket_type_code: str,
        field_key: str | None,
        filename: str,
        content: bytes,
        client_key: str,
        index: int | None = None,
    ) -> StoredDocument:
        # Se calcula una sola vez: todos los reintentos viajan con la misma clave.
        external_client_id = naming.external_client_id_for_operation(operation_code)
        document_type = naming.document_type_for(ticket_type_code, field_key)
        idempotency_key = naming.idempotency_key_for(client_key, index)
        return await self._retry.run(
            lambda: self._client.upload_document(
                content, filename, external_client_id, document_type, idempotency_key
            )
        )

    async def download_links(self, operation_code: str) -> list[DocumentRef]:
        external_client_id = naming.external_client_id_for_operation(operation_code)
        return await self._retry.run(lambda: self._client.list_documents(external_client_id))

    async def archive_ordered(
        self,
        *,
        operation_code: str | None,
        fallback_year: int,
        ticket_code: str,
        ticket_type_code: str,
        field_key: str | None,
        original_name: str,
        sequence: int,
        content: bytes,
        client_key: str,
        index: int | None = None,
    ) -> StoredDocument:
        """Archiva con el orden de la casa: carpeta por año/aeronave/correlativo y archivo numerado."""
        document_type = naming.document_type_for(ticket_type_code, field_key)
        folder = naming.folder_id_for(operation_code, fallback_year)
        filename = naming.archive_file_name(ticket_code, document_type, sequence, original_name)
        idempotency_key = naming.idempotency_key_for(client_key, index)
        return await self._retry.run(
            lambda: self._client.upload_document(
                content, filename, folder, document_type, idempotency_key
            )
        )

    async def folder_links(
        self, operation_code: str | None, fallback_year: int
    ) -> list[DocumentRef]:
        folder = naming.folder_id_for(operation_code, fallback_year)
        return await self._retry.run(lambda: self._client.list_documents(folder))
