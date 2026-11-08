DC_LOCAL = docker compose -p aero -f docker/local/docker-compose.local.yml
DC_DEV   = docker compose -p aero-dev -f docker/dev/docker-compose.dev.yml
DC_PROD  = docker compose -p aero-prod -f docker/prod/docker-compose.prod.yml
PY       = .venv/bin/python

.PHONY: help install format format-check build up up-d stop down logs ps \
        dev dev-down dev-logs seed-dev migrate-dev prod prod-down db-grants db-maintenance \
        migrate-create migrate-up migrate-down migrate-check seed pg-test \
        purge-notifications purge-personal-data sweep-uploads audit-archive \
        test test-unit test-integration test-e2e checks

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv and install dependencies
	uv venv .venv --python 3.12 || python3 -m venv .venv
	uv pip install --python $(PY) -r requirements/test.txt

## Code quality
format: ## Format with black
	$(PY) -m black -l 100 app tests alembic/env.py

format-check: ## Fail when black would change something
	$(PY) -m black -l 100 --check app tests alembic/env.py

checks: ## Architecture self-checks (every command must print nothing)
	@! grep -rn "\.session\." app/services app/api
	@! grep -rnE "^@router\.(get|post|put|patch|delete)" app/api
	@! grep -rE "from app\.(models|services|repositories)" tests/e2e

## Docker - local (api :8020, postgres 127.0.0.1:5440)
build: ## Build local images
	$(DC_LOCAL) build --no-cache
up: ## Start local stack (foreground)
	$(DC_LOCAL) up --build
up-d: ## Start local stack (background)
	$(DC_LOCAL) up --build -d
stop: ## Stop containers
	$(DC_LOCAL) stop
down: ## Stop and remove containers
	$(DC_LOCAL) down
logs: ## Tail logs
	$(DC_LOCAL) logs -f
ps: ## List containers
	$(DC_LOCAL) ps

## Docker - dev / prod
dev: ## Start dev stack
	$(DC_DEV) up --build -d
dev-down: ## Stop dev stack
	$(DC_DEV) down
dev-logs: ## Tail dev logs
	$(DC_DEV) logs -f
seed-dev: ## Seed dev (idempotent)
	$(DC_DEV) exec api python -m app.scripts.init
migrate-dev: ## Apply migrations in dev
	$(DC_DEV) exec api alembic upgrade head
prod: ## Start prod stack
	$(DC_PROD) up --build -d
prod-down: ## Stop prod stack
	$(DC_PROD) down
db-grants: ## Create the least-privilege runtime role (append-only audit): make db-grants OWNER_URL=... APP_PASSWORD=...
	psql "$(OWNER_URL)" -v app_role=aero_app -v app_password="$(APP_PASSWORD)" -f docker/postgres/audit_grants.sql

db-maintenance: ## Create the audit-maintenance role (retention only): make db-maintenance OWNER_URL=... MAINT_PASSWORD=...
	psql "$(OWNER_URL)" -v maint_role=aero_maint -v maint_password="$(MAINT_PASSWORD)" -f docker/postgres/audit_maintenance.sql

## Migrations (Alembic via CLI autogenerate only)
migrate-create: ## make migrate-create m="message"
	$(PY) -m alembic revision --autogenerate -m "$(m)"
migrate-up: ## Apply all migrations
	$(PY) -m alembic upgrade head
migrate-down: ## Roll back the last migration
	$(PY) -m alembic downgrade -1
migrate-check: ## Fail if the models differ from the migrations
	$(PY) -m alembic check

## App
seed: ## Load seeds.json (idempotent)
	$(PY) -m app.scripts.init

## Retention (cron; see docs/RETENCION.md). DRY=--dry-run to count only
purge-notifications: ## Delete notices past NOTIFICATION_RETENTION_DAYS
	$(PY) -m app.scripts.purge_notifications $(DRY)
purge-personal-data: ## Anonymize passengers of closed operations past PASSENGER_RETENTION_DAYS
	$(PY) -m app.scripts.purge_personal_data $(DRY)
sweep-uploads: ## Drop abandoned upload slots
	$(PY) -m app.scripts.sweep_uploads $(DRY)
audit-archive: ## Archive audit rows past AUDIT_RETENTION_DAYS, verify, then delete (maintenance role)
	$(PY) -m app.scripts.audit_archive $(DRY)

## Tests (need the ephemeral Postgres: make pg-test)
pg-test: ## Start throw-away Postgres on :5440 (db aero) and create aero_test
	docker run -d --rm --name aero-pg -e POSTGRES_USER=aero -e POSTGRES_PASSWORD=aero -e POSTGRES_DB=aero -p 5440:5432 postgres:16-alpine
	until docker exec aero-pg pg_isready -U aero >/dev/null 2>&1; do sleep 1; done
	docker exec aero-pg psql -U aero -d aero -c "CREATE DATABASE aero_test"
test: ## Whole suite
	$(PY) -m pytest -q
test-unit: ## Unit tests (no database)
	$(PY) -m pytest tests/unit -q
test-integration: ## Integration tests (database, may import the app)
	$(PY) -m pytest tests/integration -q
test-e2e: ## Black-box HTTP tests
	$(PY) -m pytest tests/e2e -q
