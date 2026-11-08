"""Fake en proceso de la API Maestro de Blackbox, como handler de `httpx.MockTransport`.

Sigue docs/INTEGRACION_API.md del repo de Blackbox: mismas rutas, códigos y formas JSON.
Nunca hace red real. Con `BLACKBOX_MODE=fake` lo usa la aplicación (tests E2E y desarrollo
local); en producción el arranque lo rechaza.

Fallos por nombre de archivo (solo en el fake), para probar el comportamiento ante caídas sin
tocar el código de la aplicación desde los tests. Se busca la subcadena, sin importar
mayúsculas, en el nombre con el que se archiva (el nombre original saneado va al final):

- `bbdown`: 503 siempre (el reintento se agota).
- `bbflaky`: 503 las dos primeras veces por clave de idempotencia, luego acepta.
- `bbtimeout`: tiempo de espera agotado siempre.
- `bbcred`: 401 credencial inválida.
- `bbdenied`: 403 sin permiso.
- `bblose`: guarda el documento y corta la respuesta la primera vez (el reintento recibe
  `already_stored`).
- `bbbadjson`: 201 con un cuerpo ilegible.
- `bbslow`: guarda el documento y tarda `SLOW_SECONDS` en contestar (sin bloquear el bucle de
  eventos). Sirve para probar que la API no retiene bloqueos de base de datos durante la llamada
  y para cortar la petición con el documento ya archivado (caída entre el archivo y el registro).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import uuid
from dataclasses import dataclass, field
from email.parser import BytesParser
from pathlib import PurePath

import httpx

BASE = "/api/v1/integrations/maestro/documents/"
EXTENSIONS = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
MAX_BYTES = 20 * 1024 * 1024
SLOW_SECONDS = 2.0


@dataclass
class Credential:
    company: str
    permissions: frozenset[str] = frozenset({"documents.write"})


@dataclass
class StoredDoc:
    id: str
    company: str
    filename: str
    document_type: str
    size: int
    external_client_id: str
    folder_id: str
    idempotency_key: str
    seq: int
    content: bytes = field(repr=False, default=b"")


def _err(status: int, codigo: str, detail: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail, "codigo": codigo})


def _parse_multipart(request: httpx.Request) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    raw = (
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    )
    message = BytesParser().parsebytes(raw)
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in message.get_payload():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            files[name] = (filename, payload)
        else:
            fields[name] = payload.decode()
    return fields, files


class FakeBlackbox:
    def __init__(self) -> None:
        self.credentials: dict[str, Credential] = {}
        self.docs: list[StoredDoc] = []
        self.folders: dict[tuple[str, str], str] = {}
        self.requests: list[httpx.Request] = []
        self._seq = itertools.count(1)
        # Knobs de fallo; cada uno cuenta peticiones restantes.
        self._fail: list[int] = []  # statuses a devolver sin aplicar
        self._timeouts = 0  # timeout sin aplicar
        self._lose_response = 0  # aplica y luego corta con timeout
        self._connect_errors = 0
        self._attempts: dict[str, int] = {}

    # --- configuración ---
    def add_token(self, token: str, company: str = "empresa-a", permissions=("documents.write",)):
        self.credentials[token] = Credential(company, frozenset(permissions))

    def fail_next(self, n: int = 1, status: int = 503) -> None:
        self._fail.extend([status] * n)

    def timeout_next(self, n: int = 1) -> None:
        self._timeouts += n

    def connect_error_next(self, n: int = 1) -> None:
        self._connect_errors += n

    def apply_then_lose_response_next(self, n: int = 1) -> None:
        self._lose_response += n

    def docs_of(self, company: str) -> list[StoredDoc]:
        return [d for d in self.docs if d.company == company]

    # --- transporte ---
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._delayed_handler)

    async def _delayed_handler(self, request: httpx.Request) -> httpx.Response:
        """`handler` plus the `bbslow` delay, applied after the document is stored."""
        response = self.handler(request)
        if request.method == "POST" and request.headers.get("content-type", "").startswith(
            "multipart/"
        ):
            _, files = _parse_multipart(request)
            if any("bbslow" in name.lower() for name, _ in files.values()):
                await asyncio.sleep(SLOW_SECONDS)
        return response

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._connect_errors:
            self._connect_errors -= 1
            raise httpx.ConnectError("conexión rechazada", request=request)
        if self._timeouts:
            self._timeouts -= 1
            raise httpx.ReadTimeout("timeout", request=request)
        if self._fail:
            return _err(self._fail.pop(0), "servidor", "Fallo transitorio simulado")
        response = self._route(request)
        if self._lose_response:
            self._lose_response -= 1
            raise httpx.ReadTimeout("respuesta perdida", request=request)
        return response

    # --- rutas ---
    def _authenticate(self, request: httpx.Request) -> Credential | httpx.Response:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return _err(401, "credencial_ausente", "Credencial Maestro requerida")
        credential = self.credentials.get(auth[7:].strip())
        if credential is None:
            return _err(401, "credencial", "Credencial inválida o revocada")
        if "documents.write" not in credential.permissions:
            return _err(403, "sin_permiso", "La credencial no tiene documents.write")
        return credential

    def _route(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith(BASE):
            return httpx.Response(404, json={"detail": "Not Found"})
        auth = self._authenticate(request)
        if isinstance(auth, httpx.Response):
            return auth
        rest = path[len(BASE) :]
        if rest == "" and request.method == "POST":
            return self._upload(request, auth)
        if rest == "" and request.method == "GET":
            return self._list(request, auth)
        if rest.endswith("/") and request.method == "DELETE":
            return self._delete(rest[:-1], auth)
        return httpx.Response(405, json={"detail": "Method Not Allowed"})

    def _upload(self, request: httpx.Request, cred: Credential) -> httpx.Response:
        fields, files = _parse_multipart(request)
        if "file" not in files or not fields.get("external_client_id"):
            return httpx.Response(422, json={"detail": [{"msg": "campo requerido"}]})
        client_id = fields["external_client_id"]
        if len(client_id) > 100:
            return httpx.Response(422, json={"detail": [{"msg": "demasiado largo"}]})
        filename, content = files["file"]
        key = request.headers.get("idempotency-key", "").strip()
        if not key or len(key) > 200 or any(ord(c) < 32 for c in key):
            return _err(400, "idempotencia", "Idempotency-Key ausente o inválida")
        if PurePath(filename).suffix.lower() not in EXTENSIONS:
            return _err(400, "extension", "Tipo de archivo no permitido")
        if not content:
            return _err(400, "vacio", "El archivo está vacío")
        if len(content) > MAX_BYTES:
            return _err(413, "tamano", "El archivo supera el máximo de 20 MB")
        triggered = self._triggered_failure(filename, key)
        if triggered is not None:
            return triggered
        existing = next(
            (d for d in self.docs if d.company == cred.company and d.idempotency_key == key), None
        )
        if existing:
            return self._reply(existing, "already_stored")
        folder_name = f"Maestro - {client_id}"
        folder_id = self.folders.setdefault((cred.company, folder_name), str(uuid.uuid4()))
        doc = StoredDoc(
            id=str(uuid.uuid4()),
            company=cred.company,
            filename=PurePath(filename).name,
            document_type=(fields.get("document_type") or "formulario").strip()[:100]
            or "formulario",
            size=len(content),
            external_client_id=client_id,
            folder_id=folder_id,
            idempotency_key=key,
            seq=next(self._seq),
            content=content,
        )
        self.docs.append(doc)
        if "bblose" in filename.lower() and self._attempts.get(key, 0) == 1:
            raise httpx.ReadTimeout("respuesta perdida", request=request)
        if "bbbadjson" in filename.lower():
            return httpx.Response(201, content=b"<html>no es json</html>")
        return self._reply(doc, "stored")

    def _triggered_failure(self, filename: str, key: str) -> httpx.Response | None:
        name = filename.lower()
        attempt = self._attempts[key] = self._attempts.get(key, 0) + 1
        if "bbdown" in name:
            return _err(503, "servidor", "Blackbox no disponible")
        if "bbflaky" in name and attempt <= 2:
            return _err(503, "servidor", "Fallo transitorio simulado")
        if "bbtimeout" in name:
            raise httpx.ReadTimeout("timeout")
        if "bbcred" in name:
            return _err(401, "credencial", "Credencial inválida o revocada")
        if "bbdenied" in name:
            return _err(403, "sin_permiso", "La credencial no tiene documents.write")
        return None

    @staticmethod
    def _reply(doc: StoredDoc, status: str) -> httpx.Response:
        body = {
            "data": {
                "id": doc.id,
                "filename": doc.filename,
                "status": status,
                "size": doc.size,
                "external_client_id": doc.external_client_id,
                "folder_id": doc.folder_id,
            },
            "message": "Documento recibido correctamente",
        }
        return httpx.Response(201, json=body)

    def _list(self, request: httpx.Request, cred: Credential) -> httpx.Response:
        client_id = request.url.params.get("external_client_id", "")
        if not 1 <= len(client_id) <= 100:
            return httpx.Response(422, json={"detail": [{"msg": "external_client_id inválido"}]})
        mine = [
            d for d in self.docs if d.company == cred.company and d.external_client_id == client_id
        ]
        newest = sorted(mine, key=lambda d: d.seq, reverse=True)[:100]
        data = [
            {
                "id": d.id,
                "filename": d.filename,
                "document_type": d.document_type,
                "size": d.size,
                "url": f"https://storage.fake.invalid/{d.id}?X-Signature=firmado&Expires=300",
                "created_at": f"2026-01-01T00:00:{d.seq % 60:02d}+00:00",
            }
            for d in newest
        ]
        return httpx.Response(200, json={"data": data, "message": "Documentos del cliente"})

    def _delete(self, doc_id: str, cred: Credential) -> httpx.Response:
        try:
            uuid.UUID(doc_id)
        except ValueError:
            return httpx.Response(422, json={"detail": [{"msg": "uuid inválido"}]})
        for doc in self.docs:
            if doc.id == doc_id and doc.company == cred.company:
                self.docs.remove(doc)
                return httpx.Response(204)
        return _err(404, "no_encontrado", "Documento no encontrado")


def json_body(response_or_request: httpx.Request) -> dict:
    return json.loads(response_or_request.content)
