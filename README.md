# Aeronaves Backend

API de operaciones aereas de Transvalor (Axiom Core Tech). FastAPI + SQLAlchemy async + PostgreSQL, sobre
`fastapi-basekit`. Una sola empresa: sin tenants.

Este repo contiene los **cimientos**: autenticacion, permisos, roles, usuarios, firma digital, catalogos y
auditoria, mas los clientes de integracion existentes (`app/integrations/blackbox`, `receipt_reader`).
Tipos de ticket, tickets, adjuntos y conciliacion se construyen encima (ver `CLAUDE.md`).

## Arranque rapido (sin Docker para la app)

```bash
make pg-test            # Postgres efimero en :5440 (db aero y aero_test)
make install            # crea .venv
cp env.template .env
make migrate-up         # esquema
make seed               # datos de seeds.json (usuarios demo: demo1234)
.venv/bin/uvicorn app.main:app --reload --port 8000     # http://localhost:8000/docs
```

Con Docker: `make up` (API en `:8020`, Postgres en `127.0.0.1:5440`). Dev y prod: `docker/dev`, `docker/prod`
(`make dev`, `make prod`); la base de datos no publica puertos y la app corre como usuario no root.

## Tests

```bash
make test                # unit + integration + e2e (necesita make pg-test)
make format checks       # black -l 100 y autochequeos de arquitectura
```

E2E es caja negra por HTTP; el `conftest` crea el esquema con Alembic y corre el seed de la app.

## Endpoints

`/health`, `/health/ready` · `/api/v1/auth/{login,refresh,logout,password,me,me/signature}/` · `/modules/` ·
`/permissions/` (solo lectura) · `/roles/` · `/users/` (+ `effective-permissions`, `reset-password`) ·
`/catalogs/`, `/catalog-items/` · `/audit/` (+ `/audit/export/`). Cada ruta esta en `app/config/endpoint_permissions.json` con el permiso
que la abre; el arranque aborta si una ruta montada no esta registrada.

## Produccion

`env.production.template`. Puntos criticos: `JWT_SECRET` fuerte, `DEBUG=false`, `ENABLE_DOCS=false`, orígenes CORS
explicitos, Redis para el limitador si hay mas de un proceso, rol de runtime sin UPDATE/DELETE en `audit_event`
(`make db-grants`), `SEED_USER_PASSWORD` si se siembran usuarios.

## Documentacion

- `CLAUDE.md`: reglas de capas y como agregar un modulo.
- `docs/DECISIONES.md`: desviaciones del contrato, huecos de basekit, ambiguedades.
- `docs/AUDITORIA.md`: diseño de la bitacora y su caracter append-only.
- `docs/INTEGRACION_BLACKBOX.md`, `docs/LECTURA_COMPROBANTES.md`: integraciones previas.
