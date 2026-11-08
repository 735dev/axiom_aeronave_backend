#!/usr/bin/env bash
set -e

echo "Waiting for Postgres..."
until pg_isready -h "${POSTGRES_HOST:-postgres}" -p "${POSTGRES_PORT:-5432}" >/dev/null 2>&1; do
  sleep 1
done

# Stack dump on fatal signals; `kill -USR1` dumps every thread (app.utils.observability).
export PYTHONFAULTHANDLER=1

# Migrations and the idempotent seed run under a Postgres advisory lock, so several replicas
# starting together serialise instead of racing (app/scripts/migrate.py). Alembic uses
# MIGRATION_DATABASE_URL (owner); the API itself runs with DATABASE_URL.
echo "Migrating and seeding..."
python -m app.scripts.migrate

# --forwarded-allow-ips: gunicorn/uvicorn only accept IPs or "*" (NOT CIDR ranges: a CIDR crashes the worker).
# "*" is safe here because the api is only reachable from the reverse proxy over the internal compose network;
# if you expose the api elsewhere, set FORWARDED_ALLOW_IPS to the proxy IP list.
exec gunicorn app.main:app \
  --workers "${WEB_CONCURRENCY:-2}" \
  --worker-class uvicorn_worker.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 60 \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
