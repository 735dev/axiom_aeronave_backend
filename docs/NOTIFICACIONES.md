# Notificaciones

Avisos por evento dirigidos por **rol o usuario**. El filtrado de audiencia se hace en la query
(`NotificationRepository.build_list_queryset`), nunca en Python; un aviso ajeno responde 404.

## Modelo

`notification` (aviso, sin FK al ticket para no bloquear contra la fila bloqueada), `notification_audience`
(`role_id` XOR `user_id`, restriccion CHECK) y `notification_read` (lectura **por usuario**, PK compuesta).
La API presenta `audience` como nombres de rol.

## Endpoints

- `GET /notifications/` paginado, mas nuevo primero. Filtros: `kind`, `ticket_id`, `unread`. Devuelve
  `unread` = total del usuario (no de la pagina).
- `PATCH /notifications/{id}/` con `{read: bool}` (por defecto `true`); idempotente.
- `POST /notifications/read-all/` marca todo lo mio: `{marked, unread}` (fuera del contrato).

Todos con `notifications.view`.

## Eventos (TICKET_HOOKS)

| Evento | Aviso | Audiencia |
|---|---|---|
| Alta de operacion | `operation_created` | rol asignado (y responsable) |
| Alta de hijo con rol/responsable | `ticket_assigned` | ese rol/usuario |
| Alta de INAC | `navaid_pending` | Operaciones (una vez) |
| Estado INAC no final | `navaid_pending` ("Falta radioayuda") | Operaciones |
| Intento de cierre bloqueado | `close_blocked` | Operaciones |
| Reporte tecnico | `technical_report` | Mantenimiento (+ rol asignado) |
| Comprobante leido | `receipt_validation` | Administracion |
| Operacion cerrada | `operation_closed` | Operaciones, Administracion, tripulacion, responsable |

El reporte tecnico que el sistema abre solo no avisa. Anular no se anuncia como cierre.
Deduplicacion: mismo tipo + ticket + texto en 5 minutos = un solo aviso. `close_blocked` se escribe en
una transaccion propia (el 4xx revierte la de la peticion).

## Auditoria

Consistente con `AuditedRepository` (`NotificationRepository` lo hereda, `audit_entity=notification`):

- **Creacion**: un evento `create` por aviso nuevo (`entity_label` = tipo, resumen con el codigo de la operacion y el
  numero de roles/usuarios destinatarios). Se escribe en la misma transaccion que el aviso; el aviso independiente de
  `close_blocked` lleva su evento en su propia transaccion. Un duplicado deduplicado no genera evento.
- **Lectura masiva**: `POST /notifications/read-all/` deja un evento `update` (actor = quien lee, cuantos avisos), solo si
  marco alguno. Leer uno por uno no se audita (ruido sin valor forense).
- **Purga por retencion**: evento `delete` con el conteo (ver `RETENCION.md`).
- **Contenido**: el titulo y el cuerpo citan datos del ticket y la bitacora la lee gente que no es la audiencia: **nunca**
  entran en la bitacora. Test: `tests/e2e/test_notifications_audit.py`.

## Retencion y semillas

`NOTIFICATION_RETENTION_DAYS` (90) y `python -m app.scripts.purge_notifications` (`make purge-notifications`); detalle en
`RETENCION.md`. **Semillas: no aplica**: los avisos nacen de eventos reales de tickets; uno sembrado seria una alerta falsa
visible en produccion.
