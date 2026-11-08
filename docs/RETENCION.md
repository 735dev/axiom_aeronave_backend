# Retencion de datos y privacidad

Cada tabla que crece sin tope o que guarda datos personales tiene una politica **configurable** y un **script
explicito** (cron). Nada se purga como efecto lateral de una peticion salvo la cache de idempotencia, que ya
barre el middleware. `0` en una variable de dias desactiva esa politica. Todos los scripts son idempotentes,
admiten `--dry-run` (cuentan, no cambian nada) y dejan **un evento resumen** en la bitacora (conteos, nunca contenido).

| Dato | Variable (dias) | Defecto | Script | Rol de DB | Que hace |
|---|---|---|---|---|---|
| Avisos (`notification`) | `NOTIFICATION_RETENTION_DAYS` | 90 | `app.scripts.purge_notifications` | runtime (`aero_app`) | Borra avisos anteriores; audiencia y lecturas caen en cascada |
| Datos personales de pasajeros | `PASSENGER_RETENTION_DAYS` | 1095 | `app.scripts.purge_personal_data` | runtime | Anonimiza (no borra) el manifiesto de operaciones **cerradas** con fecha contable anterior; retira los pasaportes |
| Bitacora (`audit_event`) | `AUDIT_RETENTION_DAYS` (piso `AUDIT_RETENTION_FLOOR_DAYS`=90) | 730 | `app.scripts.audit_archive` | **mantenimiento** (`aero_maint`) | Archiva, verifica y solo entonces borra |
| Cupos de subida abandonados | `UPLOAD_INTENT_STALE_HOURS` (horas) | 24 | `app.scripts.sweep_uploads` | runtime | Borra reservas que nunca se enlazaron |
| Cache de idempotencia | `IDEMPOTENCY_TTL_SECONDS` / `IDEMPOTENCY_PURGE_INTERVAL_SECONDS` | 7 dias / 10 min | (middleware) | runtime | Olvida las claves vencidas |

Los valores por defecto son un punto de partida tecnico, **no una opinion legal**: la retencion de la bitacora
y de los datos de pasajeros debe fijarla el cliente (Transvalor) con su asesoria; cambiar el numero no requiere codigo.

## Cron sugerido

```cron
# diario, fuera de horario operativo (los cuatro con el .env del servidor)
15 3 * * *  cd /srv/aero && make purge-notifications
20 3 * * *  cd /srv/aero && make purge-personal-data
25 3 * * *  cd /srv/aero && make sweep-uploads
30 3 * * 0  cd /srv/aero && make audit-archive          # semanal; usa MAINTENANCE_DATABASE_URL
```

Ver antes de ejecutar: `make purge-personal-data DRY=--dry-run`. Con Docker: `docker compose ... exec api python -m app.scripts.<x>`.

## Bitacora: archivar antes de borrar

La bitacora es de **solo anexado para la aplicacion**: el rol de runtime (`aero_app`) tiene `SELECT, INSERT` y nada mas
sobre `audit_event` (`docker/postgres/audit_grants.sql`), y ni el repositorio ni el ORM saben borrar. La retencion es la unica
via legitima por la que una fila sale, y pasa por **otro rol y otro proceso**:

| | `aero_app` (la API) | `aero_maint` (solo el script de archivo) |
|---|---|---|
| Conexion | `DATABASE_URL` | `MAINTENANCE_DATABASE_URL` |
| `audit_event` | `SELECT, INSERT` | `SELECT, INSERT, DELETE` |
| `UPDATE`, `TRUNCATE` | no | no (la historia nunca se reescribe) |
| Demas tablas | lectura/escritura de negocio | **nada** |
| Se crea con | `make db-grants` | `make db-maintenance OWNER_URL=... MAINT_PASSWORD=...` (`docker/postgres/audit_maintenance.sql`) |
| Alcanzable desde internet | si (via proxy) | no: solo cron en el servidor, credencial fuera de la imagen de la API |

Una credencial de la API filtrada no puede borrar historia; borrarla exige la credencial de mantenimiento, que no
vive en el proceso web. Sin `MAINTENANCE_DATABASE_URL` el script cae a `MIGRATION_DATABASE_URL` (el dueño): sirve en
local, en produccion debe ser `aero_maint`.

`python -m app.scripts.audit_archive` hace, en este orden, y se detiene en el primer fallo:

1. **Politica**: rechaza (codigo 2) una linea mas joven que `AUDIT_RETENTION_FLOOR_DAYS`, sea cual sea el argumento.
2. **Privilegios**: pregunta a Postgres si la conexion puede `DELETE` en `audit_event`; si no (codigo 3, p. ej. se le dio la
   URL de la API) termina **antes de escribir nada**.
3. **Archivo**: vuelca las filas anteriores a la linea a `AUDIT_ARCHIVE_DIR/audit-AAAAMMDD-seqA-B.jsonl.gz` (JSON por linea,
   todas las columnas, sin perdida) y un `.sha256` al lado. Se escribe a `.part`, `fsync` y renombra.
4. **Verificacion**: relee el archivo, compara checksum y numero de filas (codigo 4 si no cuadran; no se borra nada).
5. **Borrado**: borra exactamente las filas archivadas (por `seq`, en lotes) y anexa **en la misma transaccion** un evento
   `delete / audit` con el nombre del archivo, su sha256 y el conteo. Si el conteo borrado no coincide, la transaccion se revierte.

El directorio de archivo debe estar en un volumen respaldado y fuera del alcance de la API; es la unica copia de esa
historia. Recuperar: `zcat audit-*.jsonl.gz | jq` o reimportar con `INSERT` como dueño. Exportacion de consulta (humana,
Excel): `GET /api/v1/audit/export/` (ver `AUDITORIA.md`).

## Datos personales de pasajeros

Campos personales: nombre, documento, nacionalidad y referencia al pasaporte (`ticket_passenger`), mas el archivo del
pasaporte (`document_type=pasaporte`). `purge_personal_data` actua solo sobre operaciones **raiz en estado final** cuya
`accounting_date` es anterior a `hoy - PASSENGER_RETENTION_DAYS` (zona `BUSINESS_TIMEZONE`):

- nombre -> `Pasajero anonimizado`, documento -> `ANON-<12 hex del id>` (unico por manifiesto), nacionalidad y
  `passport_ref` -> `NULL`, claves de busqueda reemplazadas, `anonymized_at` marcado;
- la fila se queda (tamaño del manifiesto, orden, historia de la operacion): la contabilidad no cambia;
- los pasaportes de la operacion se retiran (borrado logico) del ticket: dejan de listarse y de poder descargarse;
- la busqueda `GET /passengers/search/` ya no encuentra a la persona; reintroducir datos no es posible (cerrada = inmutable, 409);
- evento `update / passenger` con conteos, sin nombres.

**Limite conocido**: el original del pasaporte vive en Blackbox, cuya API no publica borrado. Hoy solo se elimina **nuestra
referencia**; el borrado fisico requiere que Blackbox exponga `DELETE` (pedirlo antes de produccion si el cliente lo exige).
Los avisos y la bitacora no guardan nombres ni documentos de pasajeros.

## Avisos

`purge_notifications` borra `notification` anteriores a la linea (cascada a `notification_audience` y `notification_read`)
y anexa un evento resumen. No hay avisos sembrados: son consecuencia de eventos de tickets, y uno de ejemplo en el seed
aparecería como alerta falsa en produccion (decision: no aplica).

## Cupos de subida

La subida reserva un cupo (`attachment_upload`) antes de llamar a Blackbox (ver `ADJUNTOS.md`). Un cupo mas viejo que
`UPLOAD_INTENT_STALE_HOURS` ya no sera retomado por ningun reintento; `sweep_uploads` lo borra y deja un aviso en el log
con el conteo (un documento pudo llegar a Blackbox sin referencia nuestra; ese archivo huerfano no se puede borrar por API).
