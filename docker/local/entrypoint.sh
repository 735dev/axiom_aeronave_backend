#!/usr/bin/env bash
set -e

echo "Waiting for Postgres..."
until pg_isready -h "${POSTGRES_HOST:-postgres}" -p "${POSTGRES_PORT:-5432}" >/dev/null 2>&1; do
  sleep 1
done

export PYTHONFAULTHANDLER=1

echo "Running migrations and seed..."
python -m app.scripts.migrate

if [ "${DEBUG}" = "true" ] || [ "${DEBUG}" = "True" ]; then
  echo "Starting with debugpy on :5680..."
  exec python -m debugpy --listen 0.0.0.0:5680 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
