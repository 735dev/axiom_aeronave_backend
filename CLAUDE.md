# Aeronaves Backend (Axiom Core Tech, cliente Transvalor)

Backend real de operaciones aereas. Empresa unica: sin `company_id`, tenant, scopes, eventos ni billing.
Fuente de verdad, en orden: `backend-contract/{PLAN_BACKEND,API_CONTRACT}.md` + `seeds.json`
(en `~/Descargas/proyectos/Axiom/aeronaves/`), el simulador del frontend
(`frontend/src/shared/api/mock/`) y este repo.

Idioma: codigo, identificadores y comentarios en ingles; mensajes al usuario en espanol.
Sin emojis. Sin comentarios decorativos. No hacer `git add/commit/push`: Jerson commitea.

## Estado

Cimientos listos (fase 1, 2 y 6 del plan): auth, modulos, permisos, roles, usuarios con
excepciones, firma propia, catalogos, auditoria. **Pendiente para otros agentes**: tipos de
ticket, tickets, adjuntos, conciliacion, notificaciones, dinero. Ver "Como agregar un modulo".

## Comandos

```bash
make pg-test                    # Postgres efimero :5440 (db aero) + aero_test
make install                    # .venv con dependencias
make migrate-up                 # alembic upgrade head   (DATABASE_URL o MIGRATION_DATABASE_URL)
make seed                       # python -m app.scripts.init (idempotente)
make test                       # unit + integration + e2e
make format checks              # black -l 100 y autochequeos de arquitectura
make migrate-create m="..."     # SOLO autogenerate, nunca migracion a mano
make migrate-check              # alembic check: modelos == migraciones
```

Usuarios demo (`demo1234`): `super@`, `operaciones@`, `piloto@`, `mantenimiento@`, `tierra@`,
`admin@transvalor.com`.

## Capas (reglas duras)

```
app/api/v1/endpoints/*   @cbv(router) + controller. Solo HTTP. Nada de SQL ni de session.
app/api/base.py          ContractController: adapta basekit al contrato (ver DECISIONES.md)
app/services/*           Todos heredan BaseService. Logica. NUNCA tocan session ni escriben SQL.
app/repositories/*       TODO el acceso a DB. base.py: ProjectRepository / AuditedRepository.
app/models, schemas      ORM y contratos de entrada/salida (schemas/<tema>.py)
app/config/*             constantes y mapeos (settings, access, audit, catalogs, errors...)
app/exceptions/*         DomainError por tema, con STATUS_CODE_MAP
app/utils/*              helpers por tema (tokens, rate_limit, audit_diff, signature...)
app/middleware/*         ASGI puro: security_headers, access (authn + permisos)
app/scripts/init.py      seed;  seed_hooks.py = punto de extension para otros modulos
```

- Cada endpoint dentro de una clase `@cbv`; ningun `@router.get` a nivel de modulo.
- Cada controller sobreescribe `service: XService = Depends(get_x_service)` (factories en
  `app/services/dependency.py`). Se usa `super().list/retrieve/create/update/delete`; no se
  reimplementa paginacion.
- Personalizar solo por hooks: servicio `get_filters` / `search_fields` / `order_by`; repositorio
  `build_list_queryset` (scoping por fila) y `post_process_list`.
- Campos derivados de la respuesta: `@model_validator(mode="before")` del schema sobre relaciones
  `lazy="selectin"`. No mutar instancias ORM.
- Errores de dominio: subclase de `AppDomainError`; el controller usa `exc.to_http()`.
- Un recurso ajeno o inexistente responde **404**; solo el middleware de permisos responde 403.
- `update`/`delete` de basekit 0.5.5 no aceptan filtros: `ProjectRepository` los sobreescribe
  aplicando `scope_filters()` en la query.
- Si basekit no cubre un caso: documentarlo en `docs/DECISIONES.md`, no esconder un workaround.
- **Todo filtro de query debe estar en `filter_whitelist` del servicio Y declarado como
  `Query(...)` tipado en el endpoint** (basekit resuelve cualquier atributo del modelo, incluido
  `password_hash`, y no castea los no declarados).

## Permisos

- Registro `app/config/endpoint_permissions.json`: `"METHOD /ruta/{id}/"` -> `access`
  (`public|authenticated|permission`), `permissions` (cualquiera de), `prefix_match`.
- `AccessMiddleware` lo aplica; `assert_routes_registered` corre en el lifespan y aborta el
  arranque si hay ruta montada sin registrar o entrada obsoleta. Test: `tests/integration/
  test_route_registry_drift.py` + `tests/e2e/test_route_registry.py`.
- Permisos efectivos = (rol ∪ grants) − revokes, calculados por request (un cambio de rol aplica de
  inmediato). Rol superusuario: `has_all_permissions`, enlaces sincronizados con todo el catalogo.
- Catalogo de permisos CERRADO: solo seed (+ derivados por tipo de ticket via
  `PermissionService.register_type_permissions`). API de solo lectura.

## Auditoria

`AuditedRepository` (en `app/repositories/base.py`) emite un evento en create/update/delete/save
con diff campo a campo (`audit_labels`), actor/rol snapshot desde contextvar alimentado por el
token. `audit_event` es append-only (listeners ORM + rol de DB sin UPDATE/DELETE:
`docker/postgres/audit_grants.sql`). Detalle en `docs/AUDITORIA.md`.

## Como agregar un modulo (tickets, adjuntos, conciliacion)

1. Modelo en `app/models/` (importarlo en `app/models/__init__.py`) y
   `make migrate-create m="..."`.
2. Repositorio que herede `AuditedRepository` (`audit_entity`, `audit_labels`, `audit_label`);
   scoping por fila en `build_list_queryset`/`scope_filters`.
3. Servicio `ProjectService` (+ factory en `dependency.py`), schemas en `app/schemas/<tema>.py`
   con `RequestModel` para entrada (rechaza NUL, nada de modelos de DB como request).
4. Endpoints `@cbv` sobre `ContractController`, router en `app/api/v1/routers.py`.
5. **Registrar cada ruta** en `endpoint_permissions.json`.
6. Seeds: hook en `app/scripts/seed_hooks.py` (ahi estan listados los 5 tipos de ticket de
   `seeds.json["ticket_types"]` y los permisos derivados).
7. Tests E2E en `tests/e2e/` (solo HTTP, sin importar codigo del backend), incluido el cruzado
   "usuario A no ve lo de B" si el recurso tiene dueño.

## Tests

- `tests/e2e`: caja negra por HTTP (`httpx.ASGITransport`); el `conftest` prepara el esquema
  (drop + `alembic upgrade head` + `python -m app.scripts.init`). Prohibido importar
  `app.models/services/repositories` ahi (`make checks`).
- `tests/integration`: pueden importar la app (registro, append-only, privilegios de DB).
- `tests/unit`: utilidades puras y los clientes Blackbox/receipt reader (`app/integrations`, intactos).
- El bloqueo por cuenta es global al proceso: tests que fallan logins a proposito usan la fixture
  `victim` (usuario desechable), nunca un usuario semilla.
- Suite completa tambien como rol de runtime sin UPDATE/DELETE en `audit_event`:
  `TEST_APP_DATABASE_URL=postgresql+asyncpg://aero_app:<pw>@localhost:5440/aero_test make test-e2e`.

## Seguridad (baseline de la casa)

bcrypt (rondas por env), `secrets` para tokens, JWT con `iat` + `tokens_valid_after` por usuario,
rate limit de login por IP y por cuenta (store en memoria o Redis), fail-closed, errores genericos
al cliente (detalle solo al log), cabeceras de seguridad, CORS sin `*`, `DEBUG=false`, timeouts
explicitos en llamadas salientes, secretos solo por entorno. Sin default para `JWT_SECRET`: fuera
de local/test el arranque aborta si falta o es debil.

## Retencion, limites y mantenimiento

- Politicas de retencion configurables y scripts de cron (`make purge-notifications|purge-personal-data|sweep-uploads|audit-archive`):
  `docs/RETENCION.md`. La bitacora se archiva (jsonl.gz + sha256) y verifica ANTES de borrar, con el rol `aero_maint`
  (`docker/postgres/audit_maintenance.sql`), nunca con el de la API.
- Limites por cuenta e IP en `extract`, subida, busqueda de pasajeros, exportes, busqueda de bitacora y listados pesados (`ProjectService.throttle` / `list_budget`, `docs/LIMITES_DE_USO.md`).
- Arranque: `python -m app.scripts.migrate` (candado asesor de Postgres; varias replicas seguras). `fastapi<0.137` por el registro de rutas (DECISIONES §12).
- Worker colgado: `docker kill -s USR1 <contenedor>` vuelca las pilas (`docs/DESPLIEGUE.md` §8).
- `must_change_password` se aplica en `AccessMiddleware`: solo `POST /auth/password/`, `GET /auth/me/`, `POST /auth/logout/`.
  En pruebas: `activate()` completa el cambio, `issue_temporary()` entrega la temporal.
- La subida a Blackbox no retiene el bloqueo del ticket (cupo `attachment_upload`, `AttachmentRepository.checkpoint`): `docs/ADJUNTOS.md`.
- Pruebas con ajustes propios (TTL, limites): `tests/e2e/live_support.py` levanta un uvicorn con su entorno (sigue siendo HTTP).
- Escaneo de seguridad C1-C43: `docs/ESCANEO_SEGURIDAD.md`.
