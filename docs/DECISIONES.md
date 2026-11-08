# Decisiones, desviaciones y ambiguedades

Registro de todo lo que se decidio distinto al contrato (`API_CONTRACT.md`), al plan o a basekit, y de lo
que el contrato dejaba abierto. Quien construya encima debe leerlo antes de tocar auth, permisos o auditoria.

## 1. Huecos de fastapi-basekit 0.5.5 (reportar, no esconder)

| # | Hueco | Como se cubre | Donde |
|---|-------|---------------|-------|
| B1 | El envelope de basekit es `{data,message,status,pagination}` con `page`/`count`; el contrato es `{data,total,page,limit,links}`, `limit`, `q`, recursos sueltos sin envolver, listas planas para modulos/permisos/catalogos | `ContractController` sobreescribe `format_response` y `_params` (mapea `limit`->count, `q`->search). La paginacion sigue siendo la de basekit | `app/api/base.py` |
| B2 | `update`/`delete` del repositorio no aceptan filtros: no hay forma de aplicar el filtro de dueño en la query | `ProjectRepository.update/delete` aplican `scope_filters()` en la query (hoy vacio: empresa unica; el hook queda para tickets) | `app/repositories/base.py` |
| B3 | Los filtros de query resuelven CUALQUIER atributo del modelo (`?password_hash__like=$2b`) y los desconocidos se descartan con un warning: oraculo de datos ocultos | `filter_whitelist` por servicio, aplicado en `ProjectService.get_filters` | `app/services/base.py` |
| B4 | Un filtro no declarado como `Query(...)` tipado llega como `str` y revienta (500) contra columnas bool/uuid | Regla: whitelist == parametros declarados en el endpoint (documentado en CLAUDE.md) | endpoints |
| B5 | `order_by` de la query elige cualquier columna (mismo oraculo) | Se descarta en `ContractController._params`; el orden lo fija `default_ordering` | `app/api/base.py` |
| B6 | La busqueda `search` interpola el termino en `ILIKE` sin escapar (`%`, `_`) | `ProjectRepository._build_search_condition` escapa antes de delegar en super | `app/repositories/base.py` |
| B7 | `JWTService` lee `os.environ` y `decode_token` descarta `iat` (necesario para revocar) | `TokenService(JWTService)` con settings, `iat` flotante, tipo de token, HS256 fijo | `app/utils/tokens.py` |
| B8 | `page` enorme desborda int64 en el OFFSET (500) | Tope `MAX_PAGE_NUMBER` en endpoints y en `_params` | `app/config/pagination.py` |
| B9 | No hay lista completa sin paginar | Metodo propio `list_all` en servicio y repositorio (basekit lo indica asi) | `app/services/base.py` |

## 2. Contrato vs implementacion

- **Refresh**: el contrato devuelve `refresh_token` en login pero no define endpoint. Se agrego `POST /auth/refresh/`.
  Access 60 min, refresh 7 dias (env). El refresh no sirve como access y viceversa (claim `token_type`).
- Agregados fuera del contrato (necesarios para seguridad/operacion): `POST /auth/logout/`, `POST /auth/password/`,
  `PATCH /auth/me/` (`extra=forbid`: solo `full_name`, `phone`, `avatar_url`), `POST /users/{id}/reset-password/`,
  `GET /users/{id}/effective-permissions/`, `GET /health`, `GET /health/ready`.
- **Usuarios**: no hay DELETE (se desactiva). Un usuario nuevo nace con contrasena aleatoria inutilizable y
  `must_change_password=true`; el admin emite una temporal con `reset-password` (se muestra una vez, no se audita).
  Nunca viaja contrasena en el body de creacion.
- **Firma**: solo la carga su dueño (`PATCH /auth/me/signature/`, dueño tomado del token). Un PATCH de admin con
  `signature_url` no vacio responde 422. El usuario expone `has_signature`, nunca la imagen. PNG/JPEG, <=300 KB,
  el tipo se valida por magic number, no por el mime declarado; PNG con dimensiones absurdas se rechaza.
- **Menu**: `GET /modules/` solo exige sesion (todo usuario arma su menu); `PATCH` exige `roles.manage`.
- **Catalogos**: `GET /catalog-items/` y `/{id}/` exigen sesion (un piloto llena desplegables sin `catalogs.view`);
  `GET /catalogs/` exige `catalogs.view|manage`; escrituras `catalogs.manage`. `code` e `catalog` son inmutables;
  items del sistema no se borran ni desactivan; un item en uso (`CATALOG_USAGE`) no se borra (422), se desactiva.
- `aircraft_id` y `location` de usuario guardan el **codigo** del item de catalogo (como el simulador: `YV2323`,
  `charallave`), no una FK. La proteccion referencial va por `CATALOG_USAGE` en `app/config/catalogs.py`.
- `GET /roles/`, `/roles/{id}/`, `/permissions/`: cualquiera de `roles.view|roles.manage|users.view|users.manage`
  (la pantalla de usuarios los necesita).
- Borrar un id inexistente responde 404 (el contrato no define 204 idempotente).
- Errores: cuerpo `{title, detail, errors}`; titulos y detalles literales en espanol, `Accept-Language` se ignora.
  Los 404 no repiten el id solicitado.
- **Idempotency-Key**: no implementada aqui (alcance de tickets/adjuntos). CORS ya la permite.
- `audit.export` abre `GET /audit/export/` (seccion 11).
- `ticket_types` de `seeds.json` NO se cargan (modulo de tickets): ver `app/scripts/seed_hooks.py`.

## 3. Permisos y escalada de privilegios

- Permisos efectivos = (rol ∪ grants) − revokes, por request (no van en el token). `user_permission_override` tiene
  PK `(user_id, permission_id)`: una sola excepcion por permiso. Un grant ya incluido en el rol o un revoke de algo que
  el rol no tiene es 422; permiso inexistente 422.
- Techo de privilegio: quien gestiona roles/usuarios solo puede conceder (rol, grants, permisos de rol) lo que el mismo
  tiene. Nadie cambia su propio rol, estado ni excepciones por `/users/{id}`. No se desactiva ni degrada al ultimo
  superusuario activo. Los permisos del rol superusuario son inmutables; roles de sistema no se borran; un rol con
  usuarios no se borra.
- Cambiar el rol de un usuario limpia sus excepciones salvo que se envien nuevas, y revoca sus tokens.
- Desactivar usuario, cambiar rol, restablecer o cambiar contrasena y logout suben `tokens_valid_after`.
- Los 403 de permisos se registran en log (`authz_denied`), no en `audit_event`: la accion `denied` queda reservada
  para tickets (evento de negocio).

## 4. Auth

- Login: email desconocido hace verificacion bcrypt contra un hash señuelo (misma clase de tiempo) y responde igual
  que contrasena incorrecta. Lockout: 5 fallos por cuenta / 20 por IP en 15 min (env); el bloqueo se ve igual para
  cuentas reales e inventadas (429 + `Retry-After`). Solo cuentan los fallos; un login correcto resetea la cuenta.
- Store del limitador: memoria por defecto, Redis si `REDIS_URL`; si Redis cae, cae a memoria (por proceso: con N
  replicas el limite real es limite×N, por eso Redis es obligatorio al escalar; ver `env.production.template`).
- IP del cliente: la del socket; con `TRUST_FORWARDED_FOR=true` el ultimo salto de `X-Forwarded-For` (el añadido por
  nuestro proxy). Nunca el primero (lo falsifica el cliente).
- bcrypt 5 falla con >72 bytes: contrasenas mas largas se rechazan antes de hashear.
- `JWT_SECRET` sin default. Local/test: secreto efimero con aviso. Resto: falta o <32 caracteres aborta el arranque.
- No se usa ENUM nativo de PostgreSQL en ninguna tabla (strings + un CHECK en `user_permission_override.effect`).

## 5. Middleware y errores

- ASGI puro, de fuera hacia dentro: CORS -> SecurityHeaders -> Access -> router. CORS con origenes explicitos y
  `allow_credentials=False` (la API usa bearer, no cookies).
- No se registra un handler global de `Exception`: `AccessMiddleware` captura lo no manejado y devuelve un 500
  generico con la forma del contrato; el detalle solo va al log.
- Violaciones de integridad que escapan de la validacion previa (carrera de duplicados) responden 4xx generico.
- Texto con NUL: PostgreSQL no lo admite; todo schema de entrada hereda `RequestModel` y los filtros se validan (422).

## 6. Zona horaria

`from`/`to` de `/audit/` aceptan `AAAA-MM-DD` o ISO. Una fecha sola se interpreta en `BUSINESS_TIMEZONE`
(`America/Caracas`); `to=fecha` incluye el dia completo (`at < dia siguiente`). Un datetime sin zona se lee en esa zona.

## 7. Seed

`python -m app.scripts.init` es idempotente y **solo crea**: no pisa lo que un admin edito. Excepcion: el rol
superusuario se re-sincroniza con todo el catalogo. Corre bajo `without_audit()` y deja un unico evento resumen.
Usuarios demo con `demo1234` solo fuera de produccion; en produccion exige `SEED_USER_PASSWORD` (y fuerza cambio).

## 8. Ambiguedades abiertas para el cliente / siguientes agentes

- Significado de "firma electronica" en el cierre de operaciones (plan, seccion 6): hoy la firma es una imagen
  propia del usuario; el sellado criptografico no esta decidido.
- Almacenamiento de comprobantes (disco u objeto S3) y tamaño maximo: fase de archivos.
- ~~`must_change_password` solo se informa~~: ahora se **hace cumplir** (seccion 11).
- ~~Retencion de `audit_event`~~: politica y script de archivo en la seccion 11 y en `RETENCION.md`.

## 9. Tickets (nucleo)

Detalle del modelo, reglas y puntos de extension: `docs/TICKETS.md`. Desviaciones del simulador y del contrato:

- **Lecturas de comprobantes como sub-recurso.** El simulador valida sobre `/tickets/{id}/attachments/{file_id}/validate/`;
  los adjuntos no existen todavia, asi que la compuerta de revision vive en `/tickets/{id}/extractions/` (propuesta
  manual o de un lector) y `.../extractions/{id}/validate/`. El agente de adjuntos solo debe llamar a
  `TicketService.register_reading(...)` con el `attachment_id`; la regla (baja confianza sin corregir ni confirmar =
  422) ya esta aplicada en el servidor.
- **Un ticket nunca se borra.** No hay `DELETE /tickets/{id}/` (405): se anula con un estado final que no cuenta.
- **Reglas fuera de `ticket_types`.** `GET/POST /rules/` y `GET/PATCH/DELETE /rules/{id}/` exigen
  `ticket_types.manage` (el contrato no fija permiso). Listados sin paginar (arreglo), como el simulador.
- **`GET /tickets/totals/` no se construye** (pertenece al agente de conciliacion/analitica); el filtrado del listado
  esta en `TicketService.get_filters` para reutilizarlo.
- **Filtros extra.** `assigned_user_id`, `aircraft`, `from`, `to` (fecha contable), `mine` (asignado a mi o, sin
  responsable puntual, a mi rol) ademas de los del contrato.
- **Alcance por tipo, no por tenant.** El aislamiento horizontal es por tipo de ticket (`tickets.view.<tipo>`), igual que
  el simulador. Un ticket fuera de alcance responde 404 en todas las rutas (nunca 403).
- **Permisos de campo exactos.** `edit_permission`/`view_permission` de un campo se comparan con el codigo exacto: el
  global `tickets.edit` no cubre `tickets.edit.technical_report`. Superusuario los tiene todos.
- **Estado final = inmutable** tambien para `tickets.decide_any`. El intento rechazado se audita como `denied` en una
  transaccion propia (el 409 revierte la de la peticion).
- **Moneda.** Base USD. La tasa congelada solo se recalcula si cambian monto o moneda; leer o editar otro campo nunca
  revaloriza. `exchange_rate` es un campo de la operacion (Bs por USD), no del gasto.
- **Cierre de ticket que cuenta** exige: hijos bloqueantes resueltos (solo raices), autorizacion vigente si una regla
  `require_authorization` disparo, pasos `required` decididos, sin lecturas con baja confianza pendientes, y los
  `CLOSE_CHECKS` registrados por otros modulos. Anular o rechazar (`counts_as_committed=false`) nunca exige nada.
- **Idempotency-Key** se aplica a POST/PATCH/PUT/DELETE bajo `/tickets/`, `/rules/` y `/ticket-types/` (no solo POST).
  Solo se recuerdan respuestas 2xx; clave con otro cuerpo = 422 `codigo: idempotencia`; peticion en vuelo = 409.
  Almacen: tabla `idempotency_record`; TTL (`IDEMPOTENCY_TTL_SECONDS`, 7 dias) y barrido (`IDEMPOTENCY_PURGE_INTERVAL_SECONDS`, 10 min) configurables.
- **Contadores en tabla** (`ticket_counter`, UPDATE ... RETURNING atomico): un ticket anulado nunca libera su numero.
  Un contador nuevo arranca desde el mayor codigo existente de la serie.
- **Matricula de una operacion inmutable** una vez creada (el codigo la contiene): para cambiarla se anula y se crea otra.
- **Reglas sembradas** en `seeds.json["business_rules"]` (las 4 del simulador) y estado `no_findings` del reporte tecnico.
- Los permisos derivados (`tickets.view|create|edit.<tipo>`) se registran en el seed por cada tipo: el catalogo base pasa de
  15 a 30 permisos con los 5 tipos sembrados.

## Adjuntos, lectura de comprobantes y notificaciones

- **Subida multipart**, no JSON `data_url` del simulador (20 MB en base64 no cabe en el middleware de
  idempotencia ni es razonable). Respuesta 201 = ticket con `files`, como el simulador.
- **Firma de contenido manda; el MIME declarado se ignora.** Extension y bytes deben coincidir (422 `tipo`).
- **Borrado logico.** `deleted_at`; la secuencia de nombres no se reutiliza. Los documentos de Blackbox no se
  borran (el borrado no esta publicado en produccion).
- **Idempotencia de subida en el servicio**, por (ticket, usuario, clave) + hash; el `IdempotencyMiddleware` la
  omite. Sin cabecera, cada subida es distinta.
- ~~Blackbox se llama con la fila del ticket bloqueada~~: ya no; la llamada va fuera del bloqueo con cupo reservado y
  compensacion (seccion 11, `ADJUNTOS.md`).
- **Limite de tamano por `Content-Length`** (`UploadLimitMiddleware`): 413 sobre 20 MB + 64 KB de marco, 411 sin la cabecera.
- **`BLACKBOX_MODE` / `RECEIPT_READER`**: el fake se rechaza en produccion; el lector `fake` nunca se sirve en
  produccion; `anthropic` sin clave = lectura apagada.
- ~~Extract sin limite de tasa propio~~: presupuesto por cuenta e IP (seccion 11, `LIMITES_DE_USO.md`).
- **Notificaciones**: `notification.ticket_id` sin FK (evita bloqueo con la escritura independiente de
  `close_blocked`); `audience` como nombres de rol; sin semillas (no aplica), con purga y auditoria (seccion 11).
- **Eventos nuevos** `TicketEvent.CLOSE_BLOCKED` y `ATTACHMENT_ADDED`; `register_reading` acepta `status`.
- Endpoints fuera del contrato: listar/quitar adjunto, `validate` por archivo, `attachments/pending/`,
  `notifications/read-all/`, filtros `unread|kind|ticket_id`, `read:false`.

## Cierre, conciliación, pasajeros y totales

- **El cierre no es un endpoint**: sigue siendo `PATCH status_code=closed`, con `closing_check` en `CLOSE_CHECKS` y
  `seal_closing` en `STATUS_CHANGED`. El resumen `GET /tickets/{id}/closing/` es solo lectura.
- **Firma**: `ticket_decision.signed` + imagen diferida, copiada solo en el paso `signature`; el sello inmutable vive
  en `ticket_closing`. La imagen sale únicamente en el resumen de cierre. Divergencia del simulador, que la copia en
  toda decisión `done` y la expone en el ticket.
- **El hook de cierre evalúa sin alcance; el resumen con alcance** (documentado en CIERRE_Y_CONCILIACION.md).
- **Pasaporte ajeno = 422**, no se descarta en silencio como hace el simulador (200 sin el enlace).
- **Manifiesto**: reemplazo completo por `PUT`; `POST` se acepta por compatibilidad con el simulador. El simulador
  envía `passengers` como cadena JSON; aquí es una lista JSON real.
- **Búsqueda de pasajeros sin permiso de lectura = 404** (no 403, no lista vacía). El simulador devolvía 404 igual.
- **Conciliación**: errores de cierre llevan las claves abiertas en `errors` (`{clave: mensaje}`), no en `open_keys`
  como el simulador; `codigo` va en el nivel superior (convención de adjuntos).
- **Cierre y justificación calculan sin alcance; informe y exportación con alcance.** Quien cierra responde por todo el mes.
- **Candados asesores por período** (`pg_advisory_xact_lock`): compartido al editar, exclusivo al cerrar. Cierra la
  carrera "edición contra cierre" sin una tabla de bloqueos.
- **Exportación CSV por GET** (el servidor arma el archivo, inyección de fórmulas neutralizada) además del `POST`
  del simulador que solo deja el evento. Ambos auditan `export`.
- **`reconciliation.close`** es permiso nuevo del seed (módulo `analytics`, sin cambiar el conteo de módulos).
- **Validación de horas del reporte técnico en servidor** (horas iguales; llegada anterior a salida sin
  `arrival_next_day`): el simulador solo la hacía en el cliente.
- **Costura de soportes**: `TicketSupportRepository` es el único lector de adjuntos para cierre/conciliación/pasaporte.
- **Campo `passport`** (tipo `file`) sembrado en la operación y campos `actual_departure`, `actual_arrival`,
  `arrival_next_day` en el reporte técnico: el simulador los admite sin declararlos en `seeds.json`.
- **Idempotency-Key** también cubre `/reconciliation/` (cierre repetido con la misma clave repite la respuesta).
- Hueco: la suite completa tiene 4 fallos de conteo del catálogo de permisos cuando `test_attachments_upload.py`
  (otro frente) crea el tipo `sin_archivos` antes que los tests de permisos; no es de este módulo.

## 11. Endurecimiento: limites, retencion, bloqueos, contraseña temporal

- **Limites por cuenta e IP** en `extract`, subida y busqueda de pasajeros: servicio (`ProjectService.throttle`) sobre el
  mismo almacen del limitador de login (memoria o Redis opcional). 429 + `Retry-After`. Se cuenta cada llamada, antes de
  cualquier trabajo. Configurable por entorno; las pruebas en proceso los suben y un servidor propio prueba los reales
  (`tests/e2e/live_support.py`). Ver `LIMITES_DE_USO.md`.
- **Subida a Blackbox fuera del bloqueo**: tabla `attachment_upload` (cupo), commit a mitad de peticion en
  `AttachmentRepository.checkpoint` (excepcion deliberada a "un commit por peticion"; basekit no tiene otra forma de liberar
  un `FOR UPDATE` dentro de la peticion), segundo bloqueo con relectura (`get_locked(fresh=True)`), compensacion y
  reintento idempotente. Ventana asumida: ticket cerrado mientras viaja el archivo => archivo en Blackbox sin referencia.
- **Bitacora**: `GET /audit/export/` (`audit.export`) y archivo-antes-de-borrar con rol propio `aero_maint`
  (`SELECT, INSERT, DELETE` solo en `audit_event`). `aero_app` sigue sin poder borrar. El piso de retencion
  (`AUDIT_RETENTION_FLOOR_DAYS`) no se salta ni con argumentos. Nuevas entidades de bitacora: `notification`, `audit`,
  `passenger`.
- **Avisos**: `NotificationRepository` pasa a `AuditedRepository`; se audita creacion y lectura masiva sin contenido;
  purga por retencion. Semillas: no aplica.
- **`must_change_password` se aplica en `AccessMiddleware`**: con la marca solo `POST /auth/password/`, `GET /auth/me/` y
  `POST /auth/logout/` (mas las rutas publicas: login/refresh/health). Todo lo demas: **403** con `codigo: cambio_contrasena`
  (el 403 aqui no revela nada: ya hay sesion valida). Lo fija `reset-password` y la creacion de usuario; lo limpia el cambio
  de contraseña, que ademas revoca las sesiones previas. Los fixtures `activate`/`new_account` de las pruebas completan el
  cambio; `issue_temporary` entrega la temporal sin cambiarla.
- **Retencion de datos personales de pasajeros**: anonimizacion (no borrado) de operaciones cerradas antiguas. Limite: el
  original del pasaporte en Blackbox no se puede borrar por API.
- **Registro de fallos de las pruebas**: el fake de Blackbox ganó `bbslow` (guarda y tarda 2 s en responder, sin bloquear el
  bucle de eventos). Sigue siendo solo para `BLACKBOX_MODE=fake`, rechazado en produccion.

## 12. Ronda final: dependencias, matriz de roles, limites, diagnostico del cuelgue

- **Dependencias (Trivy HIGH).** `starlette` 1.10.0 (CVE-2025-62727, CVE-2026-48818, CVE-2026-54283; el ultimo se corrige en 1.3.1),
  `python-multipart` 0.0.33, `fastapi` 0.136.3. `fastapi-basekit` 0.5.5 no pone tope a FastAPI/Starlette y funciona tal cual.
  **Tope deliberado `fastapi<0.137`**: desde 0.137 `app.routes` contiene `_IncludedRouter` perezosos en vez de los `APIRoute`, y
  `endpoint_registry` (registro de permisos, fail-closed al arrancar) deja de ver las rutas. Subir FastAPI exige reescribir
  `mounted_routes`/`RouteMatcher` sobre la API de FastAPI que lo reemplace (hoy privada). Pendiente de upstream/basekit.
  Avisos de deprecacion de Starlette (`HTTP_422_UNPROCESSABLE_ENTITY`, `HTTP_413_REQUEST_ENTITY_TOO_LARGE`) en `app/exceptions/*`: cosmetico.
- **Matriz de roles (decision tomada con los documentos).** `Alcance_fase1.md` (tabla de roles y §6.5/§6.18): Mantenimiento "responde
  reportes tecnicos" y `seeds.json` marca `corrective_action`/`quotes` como "Lo llena mantenimiento" con `edit_permission:
  tickets.edit.technical_report`; Administracion "concilia el periodo". Pero el `seeds.json` del contrato no concede ese permiso derivado
  a ningun rol (solo el superusuario podia responder) y `reconciliation.close` no existe alli (es del backend). Se interpreto el texto
  del alcance como la intencion: `maintenance` recibe `tickets.edit.technical_report` (clave nueva `role_type_grants` de
  `app/fixtures/seeds.json`, aplicada tras sembrar los tipos y solo a roles creados en esa corrida) y `admin` recibe `reconciliation.close`
  **y `analytics.view`** (sin la segunda no puede abrir el informe que justifica y cierra). **Decision abierta del cliente**: si Transvalor
  quiere que el cierre del periodo lo firme otro rol (p. ej. Operaciones/Gerencia General), basta quitar ambas filas de la semilla y
  concederlo desde la administracion de roles. En bases ya sembradas el cambio no se aplica solo (la semilla no pisa roles existentes).
- **`GET /tickets/?code=`**: filtro de igualdad exacta sobre el codigo (whitelist + `Query` tipado, sin `%`/`_` como comodin, sin parcial).
  `q` sigue siendo la busqueda parcial por codigo/titulo/control.
- **`GET /attachments/pending/`: `limit` maximo 50** (`MAX_PENDING_PAGE_SIZE`), distinto del 100 del resto: cada fila lleva lectura y
  enlace a imagen. El 422 es el generico en español (`Revisa los campos marcados: limit.`, `errors.limit`). `API_CONTRACT.md` (fuera del
  repo, en `Descargas/.../backend-contract`) no menciona este endpoint ni el tope: queda documentado aqui y en `ADJUNTOS.md`; si el
  contrato se versiona, anadirlo alli.
- **`extract` con tipo inexistente o no utilizable: 404** (antes 422 y 403). Un unico mensaje (`El tipo de registro no existe.`) evita
  que el codigo de estado revele que tipos existen. Cambio de contrato: el 422 por tipo desconocido pasa a 404. `POST /tickets/` con un
  tipo que el usuario no puede crear sigue en 403 (es una escritura sobre un recurso que aun no existe, no el acceso a uno ajeno).
- **Limites nuevos** `export`, `audit_search`, `heavy_list` sobre el mismo `ActionThrottle`/almacen (`LIMITES_DE_USO.md`). `ProjectService.list_budget`
  gasta el presupuesto del servicio en cada listado paginado, antes de leer nada. Valores por defecto generosos para `heavy_list`
  (240/cuenta, 720/IP por minuto) por el NAT del hangar y el SPA; ajustar con la telemetria real.
- **Cuelgue de uvicorn al 100 % de CPU (visto una vez por el agente de frontend, no reproducido).** Causa mas probable, ahora cerrada y
  con prueba: `IdempotencyMiddleware._read_body` hacia `continue` ante cualquier mensaje que no fuera `http.request`; tras una
  desconexion `receive()` devuelve `http.disconnect` al instante y siempre, asi que el bucle nunca cedia al bucle de eventos (el worker
  deja de atender). Aplica a POST/PATCH/PUT bajo `/tickets/`, `/rules/` y `/ticket-types/` con `Idempotency-Key` cuando el cliente aborta
  (navegacion, cancelacion, red movil). **Es inferencia**: no se reprodujo el cuelgue en vivo; la prueba demuestra el bucle con un
  `receive` simulado. Hallazgo adicional real: `_FENCE_RE` del lector de comprobantes era cuadratico (160 s con 200 KB de espacios);
  reemplazado por un parseo lineal. `PYTHONFAULTHANDLER=1` y volcado por `SIGUSR1` quedan para diagnosticar el siguiente caso (`DESPLIEGUE.md` §8).
- **Migraciones con varias replicas**: `python -m app.scripts.migrate` con candado asesor de sesion (clave fija) y espera acotada; el
  candado se pierde solo si el proceso muere. Un fallo de un paso sale distinto de 0 y la replica no arranca.
