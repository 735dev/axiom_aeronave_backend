#!/usr/bin/env bash
# Simulacro de restauracion cronometrado (I6): respaldo -> restauracion en una BD vacia -> verificacion.
# NUNCA apunta a produccion: trabaja contra un contenedor Postgres efimero/de pruebas.
#
#   PG_CONTAINER=aero-pg PG_USER=aero SRC_DB=aero_e ./scripts/restore_drill.sh
#   BACKUP_FILE=/ruta/respaldo.dump ./scripts/restore_drill.sh   # restaurar un respaldo real descargado
#
# Salida: tiempos de dump y de restore, y comparacion de filas/migracion entre origen y restaurado.
# Codigo de salida != 0 si algo no coincide. RTO medido = "restore_seconds".
set -euo pipefail
export LC_ALL=C

PG_CONTAINER="${PG_CONTAINER:-aero-pg}"
PG_USER="${PG_USER:-aero}"
SRC_DB="${SRC_DB:-aero_e}"
DST_DB="${DST_DB:-${SRC_DB}_restore}"
BACKUP_FILE="${BACKUP_FILE:-}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

psql_c() { docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -v ON_ERROR_STOP=1 -tA "$@"; }
now() { date +%s.%N; }
elapsed() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%.2f", b-a}'; }

case "$DST_DB" in
  *prod*|aero) echo "Rechazado: DST_DB='$DST_DB' parece produccion/BD de trabajo." >&2; exit 2 ;;
esac

EXTERNAL=1
if [ -z "$BACKUP_FILE" ]; then
  EXTERNAL=0
  t0=$(now)
  docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -Fc -d "$SRC_DB" > "$WORKDIR/backup.dump"
  t1=$(now)
  DUMP_SECONDS=$(elapsed "$t0" "$t1")
  BACKUP_FILE="$WORKDIR/backup.dump"
else
  DUMP_SECONDS="n/a (respaldo externo)"
fi
echo "dump_seconds=$DUMP_SECONDS size_bytes=$(stat -c %s "$BACKUP_FILE")"

psql_c -d postgres -c "DROP DATABASE IF EXISTS \"$DST_DB\"" >/dev/null
psql_c -d postgres -c "CREATE DATABASE \"$DST_DB\"" >/dev/null

t0=$(now)
docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$DST_DB" --no-owner --exit-on-error < "$BACKUP_FILE"
t1=$(now)
RESTORE_SECONDS=$(elapsed "$t0" "$t1")
echo "restore_seconds=$RESTORE_SECONDS"

# Verificacion: misma version de migracion y mismo numero de filas por tabla.
rows() {
  local db="$1"
  for t in $(psql_c -d "$db" -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"); do
    echo "$t $(psql_c -d "$db" -c "SELECT count(*) FROM \"$t\"")"
  done
}
rows "$DST_DB" > "$WORKDIR/dst.txt"
if [ "$EXTERNAL" = "0" ]; then
  rows "$SRC_DB" > "$WORKDIR/src.txt"
  if diff -u "$WORKDIR/src.txt" "$WORKDIR/dst.txt"; then
    echo "verify=OK tables=$(wc -l < "$WORKDIR/dst.txt") rows=$(awk '{s+=$2} END{print s}' "$WORKDIR/dst.txt") alembic=$(psql_c -d "$DST_DB" -c 'SELECT version_num FROM alembic_version')"
  else
    echo "verify=FAIL (filas distintas entre origen y restaurado)" >&2; exit 1
  fi
else
  echo "verify=MANUAL (respaldo externo): revisar a mano"; cat "$WORKDIR/dst.txt"
  psql_c -d "$DST_DB" -c 'SELECT version_num FROM alembic_version'
fi

psql_c -d postgres -c "DROP DATABASE \"$DST_DB\"" >/dev/null
echo "cleanup=done"
