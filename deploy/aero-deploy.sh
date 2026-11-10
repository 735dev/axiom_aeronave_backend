#!/usr/bin/env bash
# Despliegue de Aeronaves en el servidor compartido. Se instala una vez como /usr/local/sbin/aero-deploy
# y es el `command=` forzado de la llave SSH de CI (ver docs/DESPLIEGUE.md, seccion 10).
#   stdin                = tar del repo (git archive)
#   $SSH_ORIGINAL_COMMAND = sha de 40 hex del commit
# Solo toca el proyecto compose `aero-prod`: nunca los stacks vecinos ni `docker system prune`.
set -euo pipefail

APP=/root/axiom_aeronave_backend
PROJECT=aero-prod
IMAGE=aeronaves-api
KEEP_RELEASES=3
HEALTH_TIMEOUT=150

SHA="${SSH_ORIGINAL_COMMAND:-${1:-}}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "deploy: sha invalido" >&2; exit 2; }
[[ -f "$APP/.env" ]] || { echo "deploy: falta $APP/.env" >&2; exit 2; }

exec 9>"$APP/.deploy.lock"
flock -n 9 || { echo "deploy: ya hay un despliegue en curso" >&2; exit 3; }

REL="$APP/releases/$SHA"
rm -rf "$REL"
mkdir -p "$REL"
tar -x --no-same-owner -C "$REL"
ln -sfn "$APP/.env" "$REL/.env"

# El compose base exige estas variables aunque Caddy quede fuera (perfil standalone).
export API_HOST=unused.invalid APP_HOST=unused.invalid ACME_EMAIL=unused@invalid.invalid
compose() {
  (cd "$1/docker/prod" && shift &&
    docker compose -p "$PROJECT" -f docker-compose.prod.yml -f docker-compose.shared-proxy.yml "$@")
}
env_value() { grep -E "^$1=" "$APP/.env" | head -1 | cut -d= -f2-; }
db_password() { sed -E 's#^[a-z+]+://[^:]+:([^@]+)@.*#\1#' <<<"$(env_value "$1")"; }

PREV="$(readlink -f "$APP/current" 2>/dev/null || true)"
docker image inspect "$IMAGE:prod" >/dev/null 2>&1 && docker tag "$IMAGE:prod" "$IMAGE:previous" || true

echo "deploy: build $SHA"
compose "$REL" build api
docker tag "$IMAGE:prod" "$IMAGE:$SHA"

echo "deploy: postgres + redis"
compose "$REL" up -d postgres redis
for _ in $(seq 1 60); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' aero-postgres-prod 2>/dev/null)" == healthy ]] && break
  sleep 2
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' aero-postgres-prod)" == healthy ]] \
  || { echo "deploy: postgres no sano" >&2; exit 1; }

echo "deploy: migraciones (rol dueno, bajo candado asesor)"
compose "$REL" run --rm --no-deps --entrypoint "" api python -m app.scripts.migrate --no-seed

echo "deploy: roles de minimo privilegio (idempotente; cubre tablas nuevas)"
PGU="$(env_value POSTGRES_USER)"; PGD="$(env_value POSTGRES_DB)"
psql_in_container() { docker exec -i aero-postgres-prod psql -v ON_ERROR_STOP=1 -U "$PGU" -d "$PGD" "$@"; }
psql_in_container -v app_role=aero_app -v "app_password=$(db_password DATABASE_URL)" \
  -f - <"$REL/docker/postgres/audit_grants.sql"
psql_in_container -v maint_role=aero_maint -v "maint_password=$(db_password MAINTENANCE_DATABASE_URL)" \
  -f - <"$REL/docker/postgres/audit_maintenance.sql"

# La semilla corre con el rol de runtime: tiene que ir despues de crearlo (primer despliegue).
echo "deploy: semilla idempotente"
compose "$REL" run --rm --no-deps --entrypoint "" api python -m app.scripts.init

echo "deploy: api"
compose "$REL" up -d --no-deps api
ok=0
for _ in $(seq 1 $((HEALTH_TIMEOUT / 3))); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' aero-api-prod 2>/dev/null)" == healthy ]] && { ok=1; break; }
  sleep 3
done

if [[ "$ok" != 1 ]]; then
  echo "deploy: la api no quedo sana; ultimas lineas:" >&2
  docker logs --tail 40 aero-api-prod >&2 || true
  if docker image inspect "$IMAGE:previous" >/dev/null 2>&1 && [[ -n "$PREV" && -d "$PREV" ]]; then
    echo "deploy: rollback a $(basename "$PREV")" >&2
    docker tag "$IMAGE:previous" "$IMAGE:prod"
    compose "$PREV" up -d --no-deps --no-build api || true
  fi
  exit 1
fi

ln -sfn "$REL" "$APP/current"
# Conserva solo los ultimos releases; nunca borra el actual ni el anterior.
ls -1dt "$APP"/releases/*/ | tail -n +$((KEEP_RELEASES + 1)) | while read -r old; do
  [[ "$(readlink -f "$old")" == "$(readlink -f "$APP/current")" ]] || rm -rf "$old"
done
echo "deploy: OK $SHA"
