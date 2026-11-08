# Despliegue, respaldo y operacion

Cubre I1-I8 de `~/.claude/SECURITY.md`. Lo que esta marcado **[verificado]** se ejecuto de verdad al escribir este
documento; lo marcado **[pendiente]** requiere el servidor real y no se pudo comprobar desde el repo.

## 1. Entornos

| Entorno | Compose | Publicado en el host | Notas |
|---|---|---|---|
| local | `docker/local/docker-compose.local.yml` | api `:8020`, postgres `127.0.0.1:5440` | `ENVIRONMENT=local`, `BLACKBOX_MODE=fake` permitido |
| dev | `docker/dev/docker-compose.dev.yml` | api `127.0.0.1:8021` | detras del proxy del servidor |
| prod | `docker/prod/docker-compose.prod.yml` | **solo Caddy 80/443** | api, postgres y redis en redes `internal` |

Redes en prod: `edge` (Caddy), `backend` (Caddy-api-redis, interna), `db` (api-postgres, interna), `egress`
(solo la api, para llamar a Blackbox/Anthropic). Postgres y Redis no tienen `ports:`; acceso directo por tunel SSH.
Firewall del VPS: default-deny, solo 22 (restringido por IP), 80 y 443.

Arranque de prod (el `.env` vive en el servidor, `chmod 600`, nunca en el repo):

```bash
cp env.production.template .env && chmod 600 .env     # rellenar CHANGE_ME y secretos
export API_HOST=api.tu-dominio ACME_EMAIL=ops@tu-dominio
make prod                                              # docker compose -p aero-prod ... up --build -d
make db-grants OWNER_URL=... APP_PASSWORD=...          # una vez tras la 1a migracion, y tras migraciones que anaden tablas
```

El entrypoint corre `python -m app.scripts.migrate` (`alembic upgrade head` con `MIGRATION_DATABASE_URL`, rol dueño, y luego la semilla
idempotente `app.scripts.init`). Los dos pasos van **bajo un candado asesor de Postgres** (`pg_try_advisory_lock`, conexion dedicada):
con varias replicas arrancando a la vez la primera migra y las demas esperan y encuentran la base ya en `head`; si la dueña del
candado muere, Postgres lo libera. La espera esta acotada por `MIGRATION_LOCK_TIMEOUT_SECONDS` (600): pasado ese tiempo la replica
falla con un mensaje claro en vez de colgarse. **[verificado]** `tests/integration/test_migrate_lock.py`: dos procesos simultaneos
(ambos salen 0, una sola aplica migraciones, 6 usuarios sembrados), espera real mientras otro retiene el candado, timeout y
liberacion tras una conexion caida. Un job previo de migracion sigue siendo valido; el candado lo hace opcional.

`docker/dev/Dockerfile.dev` instala `requirements/prod.txt` (su entrypoint arranca gunicorn); antes pedia `requirements/dev.txt`, que no existe,
y la imagen no construia.

## 2. Variables de entorno (prod)

| Variable | Secreto | Descripcion |
|---|---|---|
| `ENVIRONMENT` | no | `production` activa las validaciones de arranque (`app/config/settings.py`) |
| `DEBUG`, `ENABLE_DOCS` | no | ambas `false` en prod |
| `DATABASE_URL` | **si** | rol `aero_app` (SELECT+INSERT en `audit_event`) |
| `MIGRATION_DATABASE_URL` | **si** | rol dueño, solo Alembic |
| `POSTGRES_USER/PASSWORD/DB/HOST/PORT` | **si** (password) | init del contenedor y `pg_isready` |
| `JWT_SECRET` | **si** | >= 32 caracteres; `python -c "import secrets;print(secrets.token_urlsafe(48))"`. Rotarlo invalida todas las sesiones |
| `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `BCRYPT_ROUNDS` | no | 30 / 7 / 12 |
| `ALLOWED_ORIGINS` | no | origenes explicitos, nunca `*` |
| `REDIS_URL` | no | obligatorio con mas de un worker/replica (limitador de login y de acciones costosas) |
| `MIGRATION_LOCK_TIMEOUT_SECONDS` | no | espera maxima del candado de migraciones (600) |
| `RATE_LIMIT_*` | no | presupuestos por cuenta/IP de acciones costosas (`LIMITES_DE_USO.md`) |
| `PYTHONFAULTHANDLER` | no | `1` en imagenes y entrypoints: traza de pila ante señales fatales |
| `TRUST_FORWARDED_FOR` | no | `true` solo detras del proxy |
| `FORWARDED_ALLOW_IPS` | no | IPs o `*` (NO admite CIDR, ver hallazgos). Default `*`: la api solo es alcanzable desde Caddy |
| `SEED_USER_PASSWORD` | **si** | contraseña inicial de usuarios semilla; cambio forzado en el primer login |
| `SENTRY_DSN` | semi | vacio = desactivado |
| `BLACKBOX_MODE/BASE_URL/MAESTRO_TOKEN/TIMEOUT_SECONDS` | **si** (token) | `fake` esta prohibido en prod |
| `RECEIPT_READER`, `ANTHROPIC_API_KEY` | **si** (key) | `anthropic` u `off` |
| `API_HOST`, `APP_HOST`, `ACME_EMAIL` | no | los consume solo el Caddyfile |

Frontend: solo `VITE_PUBLIC_*` (se hornean en el bundle **publico**; jamas secretos). `VITE_PUBLIC_USE_MOCKS=false` en prod.

## 3. Manejo de secretos

- Fuera del repo: `.env` ignorado (`.env`, `.env.*` salvo plantillas). Plantillas versionadas: `env.template`, `env.production.template`.
- En el servidor: `.env` con `chmod 600` y dueño `root`/deploy. Meta: gestor de secretos (SSM/vault) que renderice el `.env` al desplegar **[pendiente]**.
- Un secreto filtrado **se rota**; borrar el commit no lo invalida. Rotacion: nuevo valor -> actualizar `.env` -> `docker compose up -d api`.
  `JWT_SECRET` invalida sesiones; `BLACKBOX_MAESTRO_TOKEN`/`ANTHROPIC_API_KEY` se rotan en el proveedor.
- CI: workflow `secret-scan` (gitleaks, historial completo) con `.gitleaks.toml` que solo permite los 4 tokens falsos documentados de los tests.
- Cuenta del proveedor (VPS/DNS/registro de imagenes/GitHub): **2FA obligatorio** en todas, con llaves o TOTP y codigos de recuperacion fuera de linea (I13) **[pendiente de confirmar por el dueño]**.

## 4. Respaldo y restauracion de Postgres

Respaldo diario (cron del servidor, formato custom, con rotacion y copia fuera del VPS):

```bash
docker exec aero-postgres-prod pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" \
  | gpg --encrypt -r backup@tu-dominio > /var/backups/aero/aero-$(date +%F).dump.gpg
find /var/backups/aero -name '*.dump.gpg' -mtime +14 -delete       # 14 dias locales; copiar a un bucket aparte
```

Restauracion real (BD vacia, nunca encima de la viva sin parar la api):

```bash
docker compose -p aero-prod -f docker/prod/docker-compose.prod.yml stop api
gpg -d aero-AAAA-MM-DD.dump.gpg | docker exec -i aero-postgres-prod pg_restore -U "$POSTGRES_USER" -d "${POSTGRES_DB}_new" --no-owner --exit-on-error
# validar, luego renombrar BD o apuntar DATABASE_URL, y `make db-grants` (los GRANT no viajan con --no-owner)
docker compose -p aero-prod -f docker/prod/docker-compose.prod.yml start api
```

### Simulacro cronometrado

`scripts/restore_drill.sh` hace dump -> restore en una BD vacia -> compara filas por tabla y version de Alembic, y borra la BD temporal.
Rechaza destinos que parezcan produccion. Con `BACKUP_FILE=` restaura un respaldo real descargado (verificacion manual).

```bash
PG_CONTAINER=aero-pg PG_USER=aero SRC_DB=aero_e ./scripts/restore_drill.sh
```

Medicion **[verificado]** 2026-11-07, Postgres 16 efimero (`aero-pg`), BD `aero_e` migrada y sembrada (28 tablas, 257 filas, 84 KB):

| Paso | Tiempo |
|---|---|
| `pg_dump -Fc` | 0.12 s |
| `pg_restore` (RTO del dato) | 0.30-0.39 s |
| Verificacion | OK: 28 tablas, 257 filas, `alembic_version=891112b36835` identicas |

Es un volumen de semilla: sirve para probar el procedimiento, **no** para prometer un RTO de produccion. Repetir el simulacro
con un respaldo real mensual y anotar aqui fecha, tamaño y tiempos **[pendiente: primer simulacro con datos reales]**. Objetivo: RPO 24 h, RTO < 1 h.

## 5. Logs, retencion y alarmas minimas

- Los contenedores usan `json-file` con rotacion (10 MB x 10). Caddy registra JSON por stdout. Enviar a un destino con **retencion >= 90 dias**
  (Loki/CloudWatch/Sentry) **[pendiente: elegir destino]**; el disco del VPS no es archivo.
- La API registra fallos de auth/autorizacion y la auditoria va a `audit_event` (append-only). No se loguean secretos ni cuerpos de adjuntos.
- Alarmas minimas (**[pendiente]** de configurar en el monitor elegido):
  1. **5xx**: tasa > 1 % en 5 min, o cualquier 5xx sostenido en `/health/ready`.
  2. **Pico de fallos de auth**: > 20 respuestas 401/429 en `/auth/login` por minuto (o `LOGIN_MAX_FAILS_*` disparandose desde varias IP).
  3. **Disponibilidad**: sonda externa a `https://API_HOST/health/ready` cada minuto.
  4. **Disco** del volumen de Postgres > 80 % y **gasto cloud** mensual con tope.

## 6. Rollback

1. Aplicacion: cada despliegue etiqueta la imagen (`aeronaves-api:<sha>`); volver = `docker compose ... up -d` con el tag previo.
2. Migracion: `alembic downgrade -1` (CI prueba up/down/up y `alembic check`). Si la migracion fue destructiva, restaurar el respaldo previo (seccion 4).
3. Frontend: reponer la imagen `aeronaves-web:<sha>` anterior; el SPA es estatico y sin estado.
4. Confirmar con `/health/ready`, un login y la lectura de un ticket; anotar el incidente.

## 7. CI y cadena de suministro

- Backend `.github/workflows/ci.yml`: black, self-checks de arquitectura, Postgres de servicio, Alembic up/down/up + check, `test_route_registry`, pytest completo, hadolint, build de la imagen prod, Trivy (imagen y fs).
- Frontend `.github/workflows/ci.yml`: `tsc`, vitest, build sin mocks, `npm audit --omit=dev`, Playwright contra el build real con API mock, hadolint, build y Trivy de la imagen web.
- `secrets.yml` (ambos repos): gitleaks sobre historial completo, semanal y en cada PR. `dependabot.yml`: pip/npm, docker y github-actions.
- Checkov: no hay Terraform/IaC; los Dockerfile pasan `checkov --framework dockerfile`. Anadir `bridgecrew/checkov-action` cuando exista IaC.
- Dependencias: `requirements/*.txt` (pineadas) son la fuente del despliegue; `uv.lock` (generado con `uv lock` desde `pyproject.toml`) es complementario. Frontend: `package-lock.json` y `npm ci`.

## 8. Diagnostico de un worker colgado al 100 % de CPU

- Las imagenes y los entrypoints fijan `PYTHONFAULTHANDLER=1` y la app registra `SIGUSR1` (`app/utils/observability.py`): es un manejador
  de C, responde aunque el bucle de eventos gire. `docker kill -s USR1 aero-api-prod` (el maestro de gunicorn lo reenvia a sus workers)
  imprime en stderr la pila de todos los hilos de cada worker; `docker logs aero-api-prod` la muestra. Hacerlo **antes** de reiniciar.
- Causa raiz encontrada y corregida en la ronda final: `IdempotencyMiddleware._read_body` giraba sin ceder si el cliente se desconectaba
  a mitad del cuerpo de un POST/PATCH/PUT con `Idempotency-Key` (`tests/unit/test_hang_guards.py::TestDisconnect`). En gunicorn el
  maestro mata al worker tras `--timeout 60` sin latido; con `uvicorn` suelto (local) nadie lo mata.

## 9. Dependencias y escaneo (2026-11-07)

- `starlette==1.10.0` (fijado aparte: FastAPI solo exige `>=0.46`), `fastapi==0.136.3`, `python-multipart==0.0.33`. **No subir FastAPI a 0.137 o
  superior** sin adaptar `app/utils/endpoint_registry.py`: desde 0.137 los routers incluidos se resuelven de forma perezosa
  (`_IncludedRouter`) y `app.routes` ya no lista los endpoints; el registro de permisos veria 0 rutas y abortaria el arranque.
- `fastapi-basekit` 0.5.5 (PyPI) instala y funciona con ese conjunto (imagen prod reconstruida, suite completa verde: 2189 pruebas).
- Trivy sobre `docker build -f docker/prod/Dockerfile.prod`: 0 HIGH/CRITICAL en paquetes Python; 46 del SO de la base, sin parche publicado.
- Roles sembrados: `maintenance` recibe `tickets.edit.technical_report` y `admin` recibe `analytics.view` + `reconciliation.close`
  **solo al crear el rol**. En una base ya sembrada hay que concederlos una vez desde la administracion de roles (o con SQL sobre
  `role_permission`); la semilla no pisa lo que un administrador cambio.
