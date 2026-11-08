# Auditoria

Bitacora transversal y de solo anexado (`audit_event`). Una sola tabla para todo el sistema; los modulos
futuros (tickets, adjuntos, conciliacion) reutilizan el mismo mecanismo.

## Como se escribe

- Desde la capa de repositorio, no desde los endpoints: `AuditedRepository` (`app/repositories/base.py`) emite un
  evento en `create`, `update`, `save` y `hard_delete`. Quien herede de el queda auditado sin acordarse de nada.
- El diff sale del historial de atributos de SQLAlchemy antes del flush y solo mira los campos de `audit_labels`
  del repositorio: hashes de contrasena e imagenes nunca entran porque no estan en esa lista.
- Actor: `Actor(id, nombre, rol)` tomado de un contextvar que `AccessMiddleware` llena con el usuario del **token**.
  Nunca se lee del cuerpo ni de cabeceras. Se guarda como foto (snapshot): renombrar al usuario o al rol despues no
  reescribe la historia. IP y user agent salen del request.
- Eventos propios: `permission_change` (rol de usuario, excepciones, permisos de un rol, permisos derivados),
  `login`, `login_failed`, `logout`, y la carga/retiro de firma (solo el hecho, nunca la imagen).
- `login_failed` se escribe con una sesion independiente (`append_independent`): el 401 aborta la transaccion del
  request y el evento debe sobrevivir. Guarda el email intentado (normalizado, recortado, sin caracteres de control)
  y la IP; nunca la contrasena. El bloqueo por exceso de intentos genera un unico evento por sujeto y ventana.
- Un PATCH que no cambia nada no genera evento.
- Seed: `without_audit()` y un solo evento resumen.

## Como se lee

`GET /api/v1/audit/` (`audit.view`). Filtros: `entity`, `entity_id`, `action`, `actor_id`, `from`, `to`, `search`
(resumen, etiqueta, actor, rol y texto del diff), `page`, `limit`. Orden fijo: `seq` descendente (secuencia de
identidad de la DB, sin huecos de reloj). Linea de tiempo de una entidad: `?entity=<x>&entity_id=<id>`.
Entidades y acciones validas: `app/config/audit.py` (valor desconocido -> 422).

## Solo anexado (tres capas)

1. **API**: no existe ninguna ruta POST/PUT/PATCH/DELETE sobre `/audit/` (405/404). Test e2e.
2. **Codigo**: `AuditEventRepository.update/delete/save/hard_delete` lanzan `AuditError`; listeners de mapper
   `before_update`/`before_delete` lanzan al intentar modificar una fila por ORM. Tests de integracion.
3. **Base de datos**: el rol de runtime (`aero_app`) tiene solo `SELECT, INSERT` sobre `audit_event`
   (`docker/postgres/audit_grants.sql`); Alembic corre con el dueño (`MIGRATION_DATABASE_URL`). Una credencial de API
   filtrada no puede reescribir ni borrar historia. Verificado: `tests/integration/test_audit_db_privileges.py` y la
   suite e2e completa pasa con ese rol (`TEST_APP_DATABASE_URL`).

Aplicar los grants: `make db-grants OWNER_URL=postgresql://aero:...@host/aero APP_PASSWORD=...` despues de cada
`alembic upgrade head` que cree tablas (los `GRANT ... ON ALL TABLES` son idempotentes).

## Para modulos nuevos

```python
class TicketRepository(AuditedRepository[Ticket]):
    audit_entity = AuditEntity.TICKET
    audit_labels = {"status": "Estado", "title": "Titulo"}   # solo lo que puede viajar a la bitacora
    def audit_label(self, instance): return instance.number
```

Acciones de negocio sin diff (cambio de estado, decision, comentario, adjunto): `self.emit(AuditEntry(...))`
con la accion correspondiente de `AuditAction`.

## Exportacion

`GET /api/v1/audit/export/` (permiso `audit.export`, distinto de `audit.view`) devuelve CSV UTF-8 **con BOM**, separador
`;`, celdas siempre entre comillas, CRLF, `Content-Disposition: attachment`, `Cache-Control: no-store`. Mismos filtros que el
listado (`entity`, `entity_id`, `action`, `actor_id`, `from`, `to`, `search`; `to=fecha` incluye el dia completo en
`BUSINESS_TIMEZONE`) y orden cronologico. Seguridad:

- **Inyeccion de formulas**: todo texto que empiece por `= + - @ tab CR` se antepone con `'` (`app/utils/csv_export.py`, el
  mismo del CSV de conciliacion). Test con `=HYPERLINK(...)`, `+cmd`, `-cmd`, `@cmd`, comillas y saltos de linea.
- Tope `AUDIT_EXPORT_MAX_ROWS` (50 000): mas de eso es 422 pidiendo acotar el rango (no se trunca en silencio).
- Nunca sale un secreto: solo viajan columnas ya limpias de la bitacora (el diff solo tiene campos rotulados).
- **La exportacion se audita**: evento `export / audit` con quien, cuantas filas y con que filtros, escrito despues de leer
  (el archivo no contiene su propia exportacion). Un 403 no deja evento.

## Retencion y archivo

Politica y procedimiento completos en `RETENCION.md`. Resumen: `AUDIT_RETENTION_DAYS` (730, piso 90) +
`python -m app.scripts.audit_archive` (`make audit-archive`), que **archiva (jsonl.gz + sha256), verifica y solo entonces
borra**, con un rol propio (`aero_maint`: `SELECT, INSERT, DELETE` en `audit_event`, nada mas) distinto del de la API
(`aero_app`: `SELECT, INSERT`). El borrado deja un evento resumen con el nombre y el hash del archivo. Pruebas de integracion:
`tests/integration/test_retention.py` (archivo verificado, piso, rol de runtime rechazado antes de escribir, privilegios
minimos del rol de mantenimiento, el runtime sigue sin poder borrar).
