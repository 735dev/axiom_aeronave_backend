# Cierre firmado, conciliación y totales

Cubre §6.6 (cierre del expediente), §6.18 (conciliación), la bitácora rápida y el filtro `mine`.
Los pasajeros están en `PASAJEROS.md`. Los adjuntos que alimentan todo esto, en `ADJUNTOS.md`.

## 1. Cierre firmado de una operación

El cierre **no es un endpoint**: es el cambio de estado normal (`PATCH /tickets/{id}/ {status_code:"closed"}`)
protegido por el hook `closing_check` (`app/services/closing_hooks.py`, registrado en `CLOSE_CHECKS`). Un hook
devuelve un mensaje en español y el servicio responde 422; no hay segunda vía para cerrar.

Bloqueos, en este orden (el primero que falla manda el mensaje):

1. Pasos obligatorios y hijos sin resolver (los resuelve el núcleo, ya existía).
2. **Gasto sin soporte**: gasto vigente (cuenta como comprometido) sin ningún archivo vivo.
3. **Comprobante sin validar**: archivo leído (`extracted`) cuya lectura nadie validó.
4. **Firma**: debe existir la decisión del paso `signature` marcada `signed` (la firma del perfil existía en el
   momento de decidir). Sin firma en el perfil: 422 "quien firma debe cargar su firma en su perfil y volver a firmar".

Anular (`cancelled`) no exige nada ni deja sello.

### La firma

`POST /tickets/{id}/decisions/` para el paso `signature` copia **la imagen del perfil de quien decide** (nunca la del
cuerpo, nunca la de otro) a `ticket_decision.signature_url` (columna diferida, no sale en listados) y marca
`ticket_decision.signed`. Solo el paso `signature` copia imagen; el resto de pasos queda `signed=false`. La lectura del
ticket expone `decisions[].signed`, jamás la imagen.

### El sello

Al pasar a `closed` el hook `seal_closing` (evento `STATUS_CHANGED`) escribe **una fila** en `ticket_closing`
(`ticket_id` único): quién firmó, su rol, cuándo, la imagen, los **totales congelados** (mismo cálculo que
`/tickets/totals/`), cantidad de archivos y carpeta de Blackbox. Es idempotente (`ON CONFLICT DO NOTHING`): dos cierres
concurrentes sellan una vez; el segundo recibe 409 porque el ticket ya es final.

- `GET /tickets/{id}` trae `closing` **sin imagen** (`has_signature: true`).
- La imagen solo viaja en `GET /tickets/{id}/closing/` (`stamp.signature_url`).
- Cambiar la firma del perfil después **no** altera lo sellado.
- Solo las raíces se sellan: cerrar un gasto no deja sello.

### Inmutabilidad

Todo ticket en estado final es inmutable (`_reject_if_locked`): 409 y evento `denied` en la bitácora, en transacción
independiente (sobrevive al rollback). Aplica a PATCH, decisiones, comentarios, adjuntos, lecturas y manifiesto.

### `GET /tickets/{id}/closing/` (resumen consolidado)

Permiso `tickets.view` (con `prefix_match`: el derivado `tickets.view.operation` basta). Un id inexistente, de otro
tipo (no raíz) o fuera del alcance responde **404**. Devuelve: `progress` (pasos hechos/total y pendientes por rol,
operación + hijos), `expenses` (ambas monedas, base congelada, `has_support`, `counts`), `totals`, `confirmed` e
`in_progress` (en base y por moneda), `supports` y `supports_by_type`, `blockers` (con `kind`, `ticket_id`,
`ticket_code`, `step_code`), `ready`, `signer` (`has_signature`, `can_sign`, paso y rol que firman), `stamp`.

Divergencia consciente: **el resumen respeta el alcance de lectura de quien consulta** (un rol que ve solo
operaciones no ve los gastos), pero **el hook de cierre evalúa el expediente completo**: no se puede cerrar con un
gasto oculto al firmante. Un resumen "listo" para un rol limitado puede aun así ser rechazado al cerrar.

## 2. Totales — `GET /tickets/totals/`

Permiso `analytics.view`. Misma consulta base que el listado (`build_list_queryset`, mismo alcance y filtros:
`q, status_code, type_code, parent_id, assigned_role_id, assigned_user_id, is_root, open, aircraft, mine, from, to`);
la agregación es un método con nombre del repositorio (`TicketAnalyticsRepository.totals`). Devuelve
`committed_base`, `pending_base`, `excluded_base`, `by_currency` (monto original y base) y `by_concept` (etiqueta del
catálogo, orden por monto desc). Importes en `Decimal`, redondeo a centavos al serializar. Se enruta **antes** de
`/tickets/{id}/` para que `totals` no se lea como id.

## 3. Conciliación (§6.18)

Todos bajo `/api/v1/reconciliation/`.

| Endpoint | Permiso | Qué hace |
|---|---|---|
| `GET /?period=AAAA-MM` | `analytics.view` | Informe del mes (filas, diferencias, totales, cierre), con el alcance del que consulta |
| `GET /periods/` | `analytics.view` | Períodos cerrados |
| `POST /acknowledgements/` | `reconciliation.close` | Justifica una diferencia (nota ≥ 5 caracteres) |
| `POST /close/` | `reconciliation.close` | Cierra el período |
| `GET /export/?period=` | `analytics.view` | CSV (UTF-8 con BOM) y evento `export` |
| `POST /export/` | `analytics.view` | Solo deja el evento (exportación armada por el cliente, como el simulador) |

`reconciliation.close` es un permiso nuevo del seed (módulo `analytics`); lo tienen el superusuario y, desde la ronda final, el rol Administración (junto con `analytics.view`; ver DECISIONES §12).

### Las tres diferencias

Port literal de `computeReconciliation` (`app/utils/reconciliation.py`, función pura probada sin base de datos):

- `expense_without_support`: sin archivos (`no_files`, severidad alta) o con lectura sin validar (`unvalidated`, media).
  Un archivo sin lectura (PDF) cuenta como soporte.
- `amount_mismatch`: lectura **validada** con monto distinto en más de un centavo (`amount`) o moneda distinta
  (`currency`). Severidad alta desde 10 % de brecha.
- `support_without_expense`: comprobante validado sin gasto de esa operación con ese monto (`no_matching_expense`),
  o sobre un gasto rechazado (`discarded_expense`).

Un gasto rechazado sin comprobante queda `discarded`, no es diferencia. La clave de cada diferencia
(`tipo:ticket:archivo`) es estable entre consultas. El gasto cae en el mes de su **fecha contable**.

### Cierre del período

- Con diferencias sin justificar: 422 `diferencias_abiertas`, las claves en `errors`.
- Período futuro: 422 `periodo_futuro`. Formato inválido: 422.
- Ya cerrado: **409** (no se reabre; no hay endpoint para hacerlo). Dos cierres concurrentes: uno 200, otro 409,
  un solo evento `Cerró` (candado asesor exclusivo + `ON CONFLICT DO NOTHING`).
- El cierre y las justificaciones se calculan **sin alcance** (sobre todo el mes): el permiso de cierre responde por el
  mes entero, no por lo que ve.
- Sello (`reconciliation_period`): quién, rol, cuándo, cantidad de líneas, total base, justificadas. Todo sale del
  token y del cálculo; los campos extra del cuerpo se ignoran.

### Inmutabilidad tras el cierre

`PeriodLockMixin` (`app/services/period_lock.py`) rechaza con 409 `codigo: periodo_cerrado` y evento `denied`:
editar o decidir sobre un gasto del período, adjuntar/quitar/validar un comprobante, registrar un gasto nuevo con fecha
en el mes cerrado y **mover** un gasto a una fecha de un mes cerrado. Solo aplica a tipos con monto; el resto de
tickets (operaciones, reportes) no se bloquea por un período. Contra carreras, escritores y cierre toman un candado
asesor `pg_advisory_xact_lock(hashtext('reconciliation:'||periodo))`: compartido los que editan, exclusivo el cierre.

### CSV

UTF-8 con BOM, separador `;`, CRLF, toda celda entre comillas con las comillas duplicadas, 15 columnas, montos con
coma decimal. **Protección contra inyección de fórmulas**: toda celda de texto que empieza con `= + - @ \t \r` lleva
una comilla simple delante (títulos y justificaciones los escribe un usuario). Los números no pasan por la guarda (un
monto negativo es legítimo). Cabeceras `Content-Disposition: attachment`, `Cache-Control: no-store`, `nosniff`.

## 4. Bitácora rápida

Sin endpoint nuevo: `POST /tickets/` con `type_code: technical_report` (+ `Idempotency-Key`) o `PATCH` parcial del
reporte abierto. El payload se **fusiona** (las llaves no enviadas se conservan). Campos nuevos sembrados en el
reporte: `actual_departure`, `actual_arrival` (HH:MM) y `arrival_next_day`. Regla en servidor
(`app/utils/quick_log.py`): horas iguales se rechazan y una llegada anterior a la salida solo vale con
`arrival_next_day`, también en un PATCH parcial (se juzga contra la hora ya guardada). `no_findings` es un estado
final que no cuenta como comprometido; el reporte queda inmutable. Una operación cerrada responde 409.

## 5. Mis misiones

`GET /tickets/?mine=true` = asignado a mí por id, **o** sin persona asignada y asignado a mi rol. La persona nombrada
manda sobre el rol (los demás del rol no lo ven). Respeta el alcance de lectura. `assigned_user_id` se valida contra
usuarios existentes (422) y se puede reasignar o limpiar por `PATCH`.

## Costura con adjuntos

Cierre, conciliación y el pasaporte leen soportes solo a través de `TicketSupportRepository`
(`app/repositories/ticket_support.py`): archivos vivos (`deleted_at IS NULL`) de `TicketAttachment` y su lectura.
Si el modelo de adjuntos cambia, es el único archivo que se toca.
