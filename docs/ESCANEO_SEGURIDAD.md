# Escaneo de seguridad del backend (C1-C43)

Protocolo de `~/.claude/SECURITY.md` ("Protocolo de ESCANEO por proyecto"). **El escaneo reporta; solo se corrigio lo que
caia dentro del alcance de este frente** (limites de uso, retencion, bloqueo de Blackbox, exportacion de bitacora,
contraseña temporal). Fecha: 2026-11-07. Evidencia como `ruta:linea` (las lineas se mueven: buscar por el simbolo).

Estado: **OK** cumple y tiene prueba o evidencia; **FALTA** hueco real; **N/A** no aplica a este sistema (con el motivo).
Los items de infraestructura (I1-I13) estan en `DESPLIEGUE.md`.

## Resumen: lo que FALTA, por (impacto / esfuerzo)

| # | Item | Hueco | Impacto | Esfuerzo |
|---|---|---|---|---|
| 1 | C25 | El limitador usa memoria si no hay `REDIS_URL`; produccion no lo exige (hoy solo avisa en el log al arrancar). Con N replicas el limite real es `limite x N` | medio al escalar | bajo (exigirlo en `security_issues` cuando haya >1 replica) |
| 2 | C42 | Los 403 (`authz_denied`) y los fallos de pago van al **log**, no a la bitacora (decision documentada en `DECISIONES.md` §3). Sin alarma definida sobre ellos | bajo | medio (alarma en el agregador de logs) |
| 3 | C33 | Dependabot cubre `pip`, `docker` y `actions`; el frontend vive en otro repo y no esta cubierto aqui | bajo | bajo (en el repo del frontend) |
| 4 | I-datos | El pasaporte original en Blackbox no se puede borrar por API: la retencion solo retira nuestra referencia (`RETENCION.md`) | medio (privacidad) | depende de Blackbox |

Nada de lo anterior es explotable sin una cuenta autenticada con permisos altos, salvo el item 1 al escalar.

## CODIGO - Acceso (A01 / API1 / API5)

| Item | Estado | Evidencia |
|---|---|---|
| C1 Filtro por dueño/tenant en la query | **OK** | Empresa unica: el aislamiento es por tipo de ticket. `app/repositories/ticket.py:46` `_visible_clause` entra en `get`, `get_locked` (`:85`) y el listado; avisos por audiencia `app/repositories/notification.py:75`; cola de adjuntos `app/repositories/attachment.py:40`; busqueda de pasajeros `app/repositories/passenger.py:82` |
| C2 Ajeno responde 404, no 403 | **OK** | 404 en tickets (`ticket_service.py` `_scoped`), avisos, adjuntos, cierre, busqueda de pasajeros sin permiso y `POST /attachments/extract/` con un tipo que el usuario no puede usar (`attachment_service.py` `extract`, identico al de un tipo inexistente) |
| C3 Test cruzado por endpoint con id | **OK** | `tests/e2e/test_tickets_scope.py`, `test_notifications.py:224`, `test_attachments_flow.py:195`, `test_closing_summary.py:58`, `test_passengers.py::TestCrossScope` (GET/PUT/POST del manifiesto y busqueda: 404 exacto), `test_attachments_reading.py` (extract) |
| C4 Endpoints admin con guard de rol | **OK** | Registro `app/config/endpoint_permissions.json` aplicado en `app/middleware/access.py:51`; el arranque aborta si hay ruta sin registrar (`app/utils/endpoint_registry.py:74`); `tests/e2e/test_route_registry.py`, `test_authorization.py`. Techo de privilegio: `user_service.py:46` |
| C5 Recursos anidados heredan el scope del padre | **OK** | Adjuntos: `attachment_service.py:124` `_attachment` resuelve primero el ticket visible; comentarios/decisiones/pasajeros pasan por `TicketService._scoped`; un adjunto de otro ticket es 404 (`test_attachments_flow.py:195`) |
| C6 Listados scopeados en el queryset | **OK** | `build_list_queryset` de ticket, aviso y adjunto (rutas de C1); `ProjectRepository.list_all` aplica `scope_filters` (`app/repositories/base.py:46`) |
| C7 Escrituras verifican propiedad | **OK** | `ProjectRepository.update/delete` aplican `scope_filters` en la query (`repositories/base.py:73`); `open_for_change` (`ticket_service.py:897`) exige edicion del tipo y ticket abierto; el marcado de lectura de avisos es solo del llamante |

## CODIGO - Entrada (API3 / A05)

| Item | Estado | Evidencia |
|---|---|---|
| C8 Schemas explicitos | **OK** | Todo request hereda `RequestModel` (`app/schemas/common.py:46`, rechaza NUL); `PATCH /auth/me/` con `extra=forbid` (`schemas/auth.py`) |
| C9 Campos de control en servidor | **OK** | `user_service.create` fija `must_change_password`, `is_active`, `password_hash` (`:135`); `test_users.py::test_create_rejects_forged_control_fields`; el actor de auditoria sale del token (`middleware/access.py`) |
| C10 Precio/monto recalculado en backend | **N/A** (parcial) | No hay catalogo de precios: el gasto ES el registro. La conversion a USD y la tasa congelada las calcula el servidor (`ticket_service.py` money, `test_tickets_money.py`); la tasa de la operacion la informa el cliente por diseno contable |
| C11 Sin SQL crudo | **OK** | Sin `text()`/SQL concatenado en `app/` (grep); busquedas con `escape_like` (`utils/search.py`), filtros con whitelist (`services/base.py`) |
| C12 Uploads | **OK** | Extension + firma de contenido + tamaño + nombre saneado (`app/utils/uploads.py:113`), limite por `Content-Length` (`middleware/upload_limit.py:22`), bomba docx; `test_attachments_upload.py`. Servidos por enlace firmado de Blackbox, nunca ejecutados |
| C13 Salida escapada | **OK** | API JSON (CSP `default-src 'none'`, `middleware/security_headers.py`); CSV neutraliza formulas (`utils/csv_export.py:14`), probado en `test_audit_export.py`, `test_reconciliation_export.py`. Sin HTML renderizado |
| C14 Redirects validados | **N/A** | No hay redirects (`grep Redirect` vacio); los clientes salientes no siguen redirects (`blackbox/client.py:94`) |

## CODIGO - Pagos e integridad (A08)

| Item | Estado | Evidencia |
|---|---|---|
| C15 Firma/timestamp de webhook | **N/A** | No hay webhooks entrantes ni pasarela de pago (grep vacio). Si se agregan: verificar antes de procesar |
| C16 Idempotencia por id de evento | **OK** (en lo que existe) | Mutaciones de tickets/reglas/tipos/conciliacion por `Idempotency-Key` (`middleware/idempotency.py:71`, TTL configurable, `test_tickets_idempotency.py`, `test_idempotency_ttl.py`); subida por clave + hash en el servicio. No hay eventos de proveedor |
| C17 El estado del pago lo decide el proveedor | **N/A** | No se cobra: la conciliacion es contable interna |
| C18 Sin deserializacion insegura | **OK** | Sin `pickle`/`yaml.load`/`marshal` en `app/` (grep vacio); solo JSON |

## CODIGO - Errores (A10)

| Item | Estado | Evidencia |
|---|---|---|
| C19 Fail-closed | **OK** | `AccessMiddleware` deniega ante cualquier fallo al decidir (`middleware/access.py:162`); sin principal el alcance es vacio (`ticket.py:46`); lector de comprobantes degrada a "ilegible", no a "aprobado" (`receipt_reader/service.py:50`) |
| C20 Ningun `except` se traga una escritura | **OK** | Los `except Exception` de `app/` re-lanzan o registran: idempotencia (`idempotency.py:132,215,223` con `logger.exception`), compensacion de cupo (`attachment_service.py` `_compensate`: la falla original sigue su camino y el fallo de limpieza se registra), limitador (`rate_limit.py`: degrada y registra) |
| C21 Error generico al cliente | **OK** | `utils/exception_handlers.py:114` (500 generico, detalle al log), `integrity_error_handler:109`; `tests/e2e/test_error_hygiene.py`; errores de Blackbox sin token ni mensaje del proveedor (`test_attachments_flow.py::test_errors_never_leak_provider_detail`) |
| C22 Rollback completo en flujos con dinero | **OK** | Una transaccion por peticion (`config/database.py` `get_db`); el cierre y la conciliacion son atomicos. Excepcion deliberada y documentada: la subida confirma el paso 1 antes de llamar a Blackbox y lo compensa (`DECISIONES.md` §11) |
| C23 Timeout explicito en llamadas salientes | **OK** | Blackbox `client.py:84` (`BLACKBOX_TIMEOUT_SECONDS`, tope 120), lector `anthropic_reader.py:83`, Redis `REDIS_TIMEOUT_SECONDS` (`settings.py`) |

## CODIGO - Auth (A07)

| Item | Estado | Evidencia |
|---|---|---|
| C24 Rate limit de login por IP y por cuenta | **OK** (exportaciones, busqueda de bitacora y listados pesados ya tienen presupuesto, `LIMITES_DE_USO.md`) | `LoginThrottle` `utils/rate_limit.py:162`, `auth_service.py:112`; `test_auth.py::TestThrottle`. Acciones costosas: `ActionThrottle` `rate_limit.py:212` (`test_rate_limits.py`) |
| C25 Store compartido si hay replicas | **FALTA** (item 1) | Redis soportado (`RedisRateLimitStore`, `ResilientStore` `rate_limit.py:103`, `settings.py:56`); no obligatorio, advertencia de arranque en produccion |
| C26 bcrypt/argon2 | **OK** | `app/config/security.py:20` (bcrypt, rondas por entorno, >72 bytes rechazado) |
| C27 `secrets` para tokens | **OK** | `utils/passwords.py:11` (`secrets.token_urlsafe`); sin `import random` en `app/` |
| C28 Expiracion corta + revocacion | **OK** | Access 30 min en produccion; `tokens_valid_after` + `iat` con subsegundos (`utils/tokens.py:40,48`, `middleware/access.py`); logout, cambio de rol/contraseña y desactivacion revocan; `test_auth.py` |
| C29 Reset de contraseña | **OK** | No hay reset por correo (sin enumeracion de usuarios). El reset lo emite un administrador: temporal aleatoria mostrada una vez, no auditada, `must_change_password=true` **aplicado** en el middleware (`access.py:126`, `test_must_change_password.py`: toda la API menos 3 rutas responde 403 `cambio_contrasena`); el cambio revoca las sesiones previas |
| C30 Sin datos sensibles en el JWT | **OK** | Claims: `sub`, `iat`, `exp`, `token_type` (`utils/tokens.py:40`); permisos y rol se leen por peticion |

## CODIGO - Egress / SSRF (dentro de A01)

| Item | Estado | Evidencia |
|---|---|---|
| C31 Guard en la capa de transporte | **N/A** | Ninguna URL la decide el usuario: Blackbox viene de `BLACKBOX_BASE_URL` (entorno) y el lector llama a un host fijo; `avatar_url` solo se guarda, nunca se descarga. Si algun dia se descarga una URL de usuario: guard en `blackbox/client.py` o un cliente compartido |
| C32 Allowlist de destinos | **N/A** | Mismo motivo; redirects desactivados (`blackbox/client.py:94`, `anthropic_reader.py:102`) |

## CODIGO - Cadena de suministro (A03)

| Item | Estado | Evidencia |
|---|---|---|
| C33 Dependabot | **OK** (item 3) | `.github/dependabot.yml:3` (pip, docker, github-actions) |
| C34 Escaneo de secretos en el historial | **OK** | `.github/workflows/secrets.yml:14` (gitleaks sobre historial completo, `.gitleaks.toml`) |
| C35 Trivy | **OK** | `.github/workflows/ci.yml:64` (imagen) y `:71` (dependencias). Reescaneo 2026-11-07 de la imagen prod reconstruida: 0 HIGH/CRITICAL en paquetes Python (starlette 1.10.0, python-multipart 0.0.33); quedan 46 HIGH/CRITICAL del SO de la base `python:3.12-slim-bookworm` (Debian 12.15) **sin parche publicado** (`affected`/`will_not_fix`): se cierran al actualizar el digest cuando Debian publique el arreglo |
| C36 Checkov/tfsec | **N/A** | Sin IaC (solo compose) |
| C37 Lockfile y pines | **OK** | `uv.lock` + `requirements/*.txt` con `==`; imagenes base por digest (`docker/prod/docker-compose.prod.yml`). Pin `fastapi<0.137` explicado en `requirements/base.txt` |

## CODIGO - Config en el repo (A02)

| Item | Estado | Evidencia |
|---|---|---|
| C38 CORS sin `*` con credenciales | **OK** | `app/main.py:80` origenes explicitos, `allow_credentials=False`; `settings.py` aborta con `*`; `test_http_security.py` |
| C39 `DEBUG=false` en produccion | **OK** | `settings.py:154` `security_issues` aborta el arranque fuera de local; docs apagadas en produccion |
| C40 HSTS, nosniff, CSP | **OK** | `middleware/security_headers.py` (HSTS fuera de local, `nosniff`, CSP `default-src 'none'`, `no-store`) |
| C41 Secretos por entorno | **OK** | `.gitignore` excluye `.env*` salvo plantillas; `env.template`, `env.production.template` versionados; `JWT_SECRET` sin default |

## CODIGO - Logs (A09)

| Item | Estado | Evidencia |
|---|---|---|
| C42 Log de fallos de auth, autorizacion y pago | **OK** (item 2) | `login_failed`/bloqueo en bitacora (`auth_service.py`, `append_independent` `audit_trail.py:99`); `authn_failed`, `authz_denied`, `password_change_required` al log (`middleware/access.py:116`); accion `denied` para intentos sobre tickets cerrados; exportaciones y purgas auditadas |
| C43 Sin secretos/tokens/tarjetas en logs | **OK** | Las lineas de log citan ids, rutas y IP, no cuerpos ni cabeceras; el token de Blackbox nunca sale (`test_security.py`, `test_attachments_flow.py`); la bitacora solo guarda campos rotulados (sin hashes ni imagenes), `test_audit.py::test_secrets_never_enter_the_trail`; avisos sin contenido en la bitacora (`test_notifications_audit.py`) |

## Lo cerrado en este frente (con su prueba)

| Hueco previo | Cierre | Prueba |
|---|---|---|
| `extract`/subida/busqueda sin limite propio | `ActionThrottle` por cuenta e IP, 429 + `Retry-After` | `tests/e2e/test_rate_limits.py`, `tests/unit/test_retention_units.py` |
| Avisos sin semilla, purga ni auditoria | Auditoria sin contenido + purga + (semillas: N/A) | `test_notifications_audit.py`, `tests/integration/test_retention.py` |
| Blackbox con la fila del ticket bloqueada | Tres pasos, cupo reservado, compensacion | `test_attachments_lock.py` |
| Bitacora sin exportacion ni retencion | `GET /audit/export/` + archivo-antes-de-borrar con rol propio | `test_audit_export.py`, `tests/integration/test_retention.py` |
| `must_change_password` informativo | Aplicado en el middleware | `test_must_change_password.py` |
| TTL de idempotencia fijo | `IDEMPOTENCY_TTL_SECONDS` | `test_idempotency_ttl.py` |
| Datos de pasajeros sin retencion | Anonimizacion por politica | `test_retention_personal_data.py` |

## Ronda final (agente H, 2026-11-07)

| Hueco previo / hallazgo | Cierre | Prueba |
|---|---|---|
| Trivy HIGH en `starlette` 0.47.3 (CVE-2025-62727, CVE-2026-48818, CVE-2026-54283) y `python-multipart` 0.0.20 (CVE-2026-24486, -42561, -53539) | `starlette==1.10.0` (fijado aparte), `fastapi==0.136.3`, `python-multipart==0.0.33`; `fastapi-basekit` 0.5.5 compatible sin cambios | suite completa verde; Trivy de la imagen: 0 en Python |
| Sin presupuesto en exportes, busqueda de bitacora y listados pesados (antes item 1 de la tabla) | Acciones `export`, `audit_search`, `heavy_list` sobre el mismo `ActionThrottle` | `test_rate_limits.py` (cuenta, IP, ventana) |
| `extract` respondia 403 para un tipo sin permiso (antes item 2) | 404 identico al de un tipo inexistente | `test_attachments_reading.py` |
| Falta de test cruzado en el manifiesto (antes item 3) | `TestCrossScope` | `test_passengers.py` |
| **ReDoS**: `_FENCE_RE` del lector de comprobantes era cuadratico (160 s con 200 KB de espacios tras ```json; el texto lo puede dirigir lo impreso en el comprobante) | Parseo lineal por `split`, tope de arranques de objeto, `RecursionError` capturado | `tests/unit/test_hang_guards.py` |
| **Bucle activo**: `IdempotencyMiddleware._read_body` giraba sin ceder cuando el cliente se desconectaba a mitad de la peticion (`receive()` devuelve `http.disconnect` al instante y para siempre) = el uvicorn al 100 % de CPU reportado | Un `http.disconnect` termina la lectura y la peticion se descarta sin respuesta | `test_hang_guards.py::TestDisconnect` |
| Cuerpo JSON anidado miles de niveles daba 500 en `canonical` | `RecursionError` tratado como "no es JSON normalizable" | `test_hang_guards.py::TestHostileBodies` |
| Migraciones al arrancar varias replicas sin coordinacion (C-despliegue) | `python -m app.scripts.migrate`: candado asesor de Postgres alrededor de `alembic upgrade head` y la semilla | `tests/integration/test_migrate_lock.py` |

Revisados y sin hallazgo: patrones de `schemas/user.py`, `utils/signature.py`, `utils/uploads.py`, `utils/passengers.py`, catalogos, periodos,
claves de idempotencia (todos anclados y sin cuantificadores anidados; medidos con entradas de 100-400 KB), bucles de reintento
de Blackbox y del lector (acotados por `max_retries`), `RoleService._unique_code` (acotado por el numero de roles).
Residual conocido: `MemoryRateLimitStore` solo purga una clave al volver a leerla (crece con IPs distintas hasta el reinicio; con Redis no aplica).
