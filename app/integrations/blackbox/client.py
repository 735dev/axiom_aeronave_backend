"""Cliente async de la API Maestro de Blackbox (solo servidor; el token no sale de aquí)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import rules
from .errors import (
    NO_ENCONTRADO,
    RED,
    RESPUESTA_INVALIDA,
    TIMEOUT,
    BlackboxError,
    BlackboxNetworkError,
    BlackboxNotFoundError,
    from_response,
)

logger = logging.getLogger(__name__)

DOCUMENTS_PATH = "/api/v1/integrations/maestro/documents/"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_MIME = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@dataclass(frozen=True)
class StoredDocument:
    id: str
    filename: str
    status: str
    size: int
    external_client_id: str
    folder_id: str | None

    @property
    def already_stored(self) -> bool:
        return self.status == "already_stored"


@dataclass(frozen=True)
class DocumentRef:
    id: str
    filename: str
    document_type: str | None
    size: int
    url: str  # enlace firmado de ~5 min: no se persiste
    created_at: str


def _check_base_url(base_url: str) -> str:
    parts = urlsplit(base_url or "")
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        pass
    elif parts.scheme == "http" and host in _LOCAL_HOSTS:
        pass
    else:
        # Sin eco de la URL: podría traer credenciales embebidas.
        raise ValueError("BLACKBOX_BASE_URL debe ser https (http solo para localhost)")
    if parts.username or parts.password:
        raise ValueError("BLACKBOX_BASE_URL no debe llevar credenciales")
    return base_url.rstrip("/")


class BlackboxClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = _check_base_url(base_url)
        if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("Token Blackbox ausente o con caracteres inválidos")
        if timeout <= 0:
            raise ValueError("timeout debe ser > 0")
        self._token = token
        self._timeout = timeout
        # follow_redirects=False: un redirect no debe arrastrar el Authorization a otro host.
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(timeout),
        )

    def __repr__(self) -> str:
        return f"BlackboxClient(base_url={self._base_url!r}, token=<oculto>)"

    async def __aenter__(self) -> "BlackboxClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})}
        try:
            # Timeout explícito en cada llamada saliente.
            response = await self._http.request(
                method, url, headers=headers, timeout=httpx.Timeout(self._timeout), **kwargs
            )
        except httpx.TimeoutException as exc:
            logger.warning("Blackbox %s: timeout (%s)", method, type(exc).__name__)
            raise BlackboxNetworkError(TIMEOUT, "Tiempo de espera agotado") from None
        except httpx.HTTPError as exc:
            logger.warning("Blackbox %s: fallo de red (%s)", method, type(exc).__name__)
            raise BlackboxNetworkError(RED, "No se pudo conectar con Blackbox") from None
        if response.is_success:
            return response
        error = from_response(response.status_code, response.content, secrets=(self._token,))
        logger.warning("Blackbox %s: HTTP %s [%s]", method, error.status, error.codigo)
        raise error

    @staticmethod
    def _json_data(response: httpx.Response) -> Any:
        try:
            return response.json()["data"]
        except (ValueError, KeyError, TypeError):
            raise BlackboxError(
                response.status_code, RESPUESTA_INVALIDA, "Respuesta de Blackbox ilegible"
            ) from None

    async def upload_document(
        self,
        content: bytes,
        filename: str,
        external_client_id: str,
        document_type: str,
        idempotency_key: str,
    ) -> StoredDocument:
        ext = rules.validate_upload(
            content, filename, external_client_id, document_type, idempotency_key
        )
        response = await self._request(
            "POST",
            DOCUMENTS_PATH,
            headers={"Idempotency-Key": idempotency_key},
            files={"file": (PurePath(filename).name, content, _MIME[ext])},
            data={"external_client_id": external_client_id, "document_type": document_type},
        )
        data = self._json_data(response)
        try:
            return StoredDocument(
                id=str(data["id"]),
                filename=str(data["filename"]),
                status=str(data["status"]),
                size=int(data["size"]),
                external_client_id=str(data["external_client_id"]),
                folder_id=None if data.get("folder_id") is None else str(data["folder_id"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise BlackboxError(
                response.status_code, RESPUESTA_INVALIDA, "Respuesta de Blackbox incompleta"
            ) from None

    async def list_documents(self, external_client_id: str) -> list[DocumentRef]:
        rules.validate_external_client_id(external_client_id)
        response = await self._request(
            "GET", DOCUMENTS_PATH, params={"external_client_id": external_client_id}
        )
        data = self._json_data(response)
        try:
            return [
                DocumentRef(
                    id=str(item["id"]),
                    filename=str(item["filename"]),
                    document_type=item.get("document_type"),
                    size=int(item["size"]),
                    url=str(item["url"]),
                    created_at=str(item["created_at"]),
                )
                for item in data
            ]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise BlackboxError(
                response.status_code, RESPUESTA_INVALIDA, "Lista de Blackbox incompleta"
            ) from None

    async def delete_document(self, document_id: str) -> None:
        # Solo UUID: evita que un id raro altere la ruta (../, ?, etc.).
        try:
            safe_id = str(uuid.UUID(str(document_id)))
        except ValueError:
            raise BlackboxNotFoundError(404, NO_ENCONTRADO, "Documento no encontrado") from None
        await self._request("DELETE", f"{DOCUMENTS_PATH}{safe_id}/")
