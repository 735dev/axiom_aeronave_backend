# Integración con Blackbox

Módulo `app/integrations/blackbox/`: cliente servidor-a-servidor para archivar adjuntos de
Aeronaves en Blackbox (API Maestro). Contrato fuente: `docs/INTEGRACION_API.md` del repo de Blackbox.

## Módulos

| Archivo | Rol |
|---|---|
| `errors.py` | `BlackboxError` (`status`, `codigo`, `detail`, `transient`) y subclases credencial / validación / no encontrado / red. Redacta tokens. |
| `rules.py` | Pre-validación local con los límites de Blackbox (extensión, 20 MB, vacío, clave <= 200, `external_client_id` 1..100). No envía nada. |
| `naming.py` | Funciones puras: operación -> `external_client_id`, ticket/campo -> `document_type`, clave de idempotencia. |
| `client.py` | `BlackboxClient` (httpx async): `upload_document`, `list_documents`, `delete_document`. |
| `service.py` | `AttachmentVault` + `RetryPolicy`: lo que usa el resto del backend. |

## Mapeo Aeronaves <-> Blackbox

- **Carpeta**: el código de operación (`YV2323-004-26`) es el `external_client_id`; Blackbox crea
  `Maestro - YV2323-004-26`. Solo `[A-Za-z0-9._-]`, 1 a 100 caracteres.
- **document_type** (por tipo de ticket / campo):

  | Ticket | Campo | document_type |
  |---|---|---|
  | expense | support | `comprobante` |
  | technical_report | photo | `evidencia_tecnica` |
  | operation | manifest | `manifiesto` |
  | inac_permit | approval | `permiso_inac` |
  | hangar_incident | photo | `evidencia_hangar` |
  | cualquiera | passport | `pasaporte` |
  | otro | - | `comprobante` |

- **Idempotency-Key**: `aeronaves:{client_key}` o `aeronaves:{client_key}:a{index}`. Determinista:
  el mismo adjunto lógico siempre produce la misma clave, así un reintento no duplica. Si la clave
  supera 200 caracteres o `client_key` trae caracteres fuera del charset seguro, se usa
  `aeronaves:h:{sha256}`. `client_key` debe identificar el adjunto (p. ej. id del ticket + campo),
  nunca ser compartido entre archivos distintos.

## Reintentos

`RetryPolicy(max_retries=3, base_delay=0.5)`: solo errores transitorios (red, timeout, 408, 429,
>= 500), con backoff exponencial y **la misma clave**. Errores 4xx nunca se reintentan. Si se agotan,
`AttachmentVault` re-lanza el `BlackboxError`; nunca lo traga (el llamador decide qué hacer con el
adjunto pendiente).

## Seguridad

- El token (`bb_maestro_...`) vive solo en la variable de entorno del servidor
  `BLACKBOX_MAESTRO_TOKEN`. Nunca en el frontend, el repo, logs ni mensajes de error.
- Base URL `https` obligatoria (`http` solo para localhost); no se siguen redirects.
- Timeout explícito en cada llamada.
- Los tests usan un fake en proceso (`app/integrations/blackbox/fake.py`, reexportado por `tests/unit/fake_blackbox.py`) y un token falso. **Nunca se prueba
  contra producción.** El smoke test es opt-in (ver abajo).
- Las URL de descarga (`url`) caducan en ~5 min: no se guardan.

## Variables de entorno

| Variable | Uso |
|---|---|
| `BLACKBOX_BASE_URL` | Ej. `https://blackboxve.store` o `http://localhost:8003`. |
| `BLACKBOX_MAESTRO_TOKEN` | Credencial Maestro de la empresa. |
| `BLACKBOX_TIMEOUT_SECONDS` | Timeout por llamada (por defecto 15). |
| `BLACKBOX_MODE` | `real` (por defecto) o `fake`. `fake` usa el Blackbox en proceso; **se rechaza en produccion** (`security_issues`). Sin configuracion en modo real, subir un archivo responde 503 (falla cerrado). |
| `BLACKBOX_RETRY_BASE_DELAY` | Espera base del backoff (segundos). Los tests la bajan a milisegundos. |

## Uso en la API

El servicio `AttachmentService` (ver `ADJUNTOS.md`) crea el cliente por peticion con
`build_client(settings)`/`build_vault(...)` (`app/integrations/blackbox/factory.py`) y lo cierra al terminar.
La clave de idempotencia hacia Blackbox es `sha256(usuario|ticket|Idempotency-Key)`: un reintento tras una
respuesta perdida encuentra el mismo documento. Un fallo de Blackbox responde 503 `codigo: almacenamiento`
con mensaje generico; el detalle (estado, codigo) solo va al log.

El fake falla a demanda segun el nombre del archivo (solo en `BLACKBOX_MODE=fake`): `bbdown` (503),
`bbflaky` (503 dos veces por clave), `bbtimeout`, `bbcred` (401), `bbdenied` (403), `bblose` (guarda y
agota el tiempo en el primer intento) y `bbbadjson`.

## Uso

```python
client = BlackboxClient(base_url, token, timeout=float(timeout))
vault = AttachmentVault(client)
doc = await vault.archive_attachment("YV2323-004-26", "expense", "support",
                                     "factura.pdf", content, client_key="tk-123-support", index=0)
links = await vault.download_links("YV2323-004-26")
```

## Tests

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -m black --check -l 100 app tests scripts
```

Smoke opt-in (no se corre en CI): `python scripts/blackbox_smoke.py` con las dos variables
definidas. Contra `blackboxve.store` exige `--allow-production` y `BLACKBOX_SMOKE_CONFIRM=yes`.
