# Tickets: modelo, reglas y extension

Todo el trabajo (operaciones, gastos, reportes, permisos, novedades) es un ticket de un **tipo**. El tipo define el
formulario, los estados, los pasos de aprobacion y las relaciones. Fuente de verdad del comportamiento: el simulador
`frontend/src/shared/api/mock/` y `backend-contract/API_CONTRACT.md`.

## Modelo

| Tabla | Que guarda |
|---|---|
| `ticket_type` | codigo, nombre, `is_root`, `child_type_codes`, rol por defecto, `blocks_parent_close`, `relation_kinds`, `is_system`, `is_active` |
| `ticket_type_field` | clave, etiqueta, tipo, `required`, opciones/catalogo, `role` semantico, `view_permission`/`edit_permission` |
| `ticket_type_status` | codigo, color, `is_initial`, `is_final`, `counts_as_committed`, `next_codes` (transiciones; nulo = libres) |
| `ticket_type_step` | pasos de aprobacion: rol responsable, `required` |
| `ticket` | codigo, tipo, estado, padre, responsable (rol y/o usuario), `payload` JSONB, columnas de dinero congelado, `rule_hits`, `authorization` |
| `ticket_decision` / `ticket_comment` | una decision por paso; comentarios |
| `ticket_extraction` | lectura de un comprobante pendiente de validar (propuesta) |
| `business_rule` | reglas por tipo |
| `ticket_counter` | series de numeracion |
| `idempotency_record` | cache de respuestas por `Idempotency-Key` |

Roles semanticos de campo (uno por rol y tipo, validado en el servidor): `amount`, `currency`, `accounting_date`,
`aircraft`, `item`, `quantity`, `unit`, `supplier`. Los totales, la numeracion y las reglas leen el **rol**, nunca el nombre
del campo. `currency` exige `amount`; `unit` exige `quantity`.

## Alcance y permisos

- `tickets.view|create|edit` globales cubren todos los tipos; `tickets.<accion>.<tipo>` cubre uno. Se generan al crear el
  tipo y se retiran al borrarlo.
- Listados y lecturas se acotan **en la consulta** (`TicketRepository.build_list_queryset/get`). Fuera de alcance = 404.
- Crear/editar valida el permiso por tipo en el servicio (403 si no lo tiene pero el ticket es visible).
- Un campo con `edit_permission` que el usuario no tiene rechaza toda la peticion con 403; con `view_permission` ausente
  no aparece en la respuesta.
- Decisiones: el paso debe ser del rol del usuario, salvo `tickets.decide_any`.

## Estados y cierre

El estado inicial lo fija el servidor. Un ticket en estado final es **inmutable** (409 para PATCH, decisiones,
autorizacion, lecturas), sin excepcion de rol; el intento queda en la bitacora como `denied`.

Pasar a un final que cuenta (`counts_as_committed`) exige, ademas de la transicion valida:

1. raiz: ningun hijo con `blocks_parent_close` sin resolver (422 con sus codigos);
2. autorizacion vigente si una regla `require_authorization` esta disparada;
3. todos los pasos `required` con decision;
4. ninguna lectura con baja confianza sin validar;
5. todos los `CLOSE_CHECKS` registrados.

Anular/rechazar (final que no cuenta) no exige nada. Al crear una operacion nace su **reporte tecnico** abierto, asignado al
rol piloto; `no_findings` lo resuelve sin pasos de mantenimiento.

## Dinero

Moneda base USD. Al crear/editar un ticket con rol `amount` se guarda `money` (monto, moneda, tasa, `amount_base`,
`rated_at`, `rate_source`). La tasa sale de `exchange_rate` de la operacion padre (Bs por USD); `rate = 1/exchange_rate`.
Sin tasa utilizable: 422, nunca se convierte con 1. La tasa queda **congelada**: solo se recalcula si cambian monto o
moneda. Leer, listar o editar otro campo no revaloriza. El cliente nunca envia `amount_base`, `rate` ni `money`.

## Numeracion

Raiz con rol `aircraft`: `MATRICULA-NNN-AA` (correlativo por aeronave y ano contable) y numero de control `TVOA N°NNNN-AA`.
Resto: `TK-NNNN` global. Contadores atomicos en `ticket_counter`; concurrencia segura; un numero nunca se reutiliza.

## Reglas de negocio

Campos: `amount_base` (gt/lt/eq), `attachments` (missing), `concept` (eq), `amount_vs_route` (gt con `route_limits`
`{"CCS-PZO": 1200}`; la ruta sale de origen/destino del padre; una ruta sin tope no inventa uno). Acciones: `warn`,
`require_support`, `require_authorization`. Se evaluan al crear y al editar; cada disparo nuevo se audita una vez; el texto
avisado y la fecha del disparo se congelan; un disparo que ya no aplica se retira.

`POST /tickets/{id}/authorize/` (`tickets.decide_any`) sella `{por, rol, fecha, nota, amount_base}`. Cambiar el monto la
invalida (`missing_authorization` compara el monto sellado con el vigente).

## Compuerta de revision

`POST /tickets/{id}/extractions/` guarda una lectura (campos con `confidence` low/high). `.../validate/` recibe los valores
aprobados y `confirmed` (claves revisadas). Un campo de baja confianza que no se corrigio ni se confirmo = 422 nombrandolo.
Validar aplica los valores al payload (misma validacion que un PATCH), reevalua dinero y reglas, y audita cada correccion.

## Idempotency-Key

Cabecera opcional en mutaciones bajo `/tickets/`, `/rules/`, `/ticket-types/`. Clave `actor|ruta|clave` + huella
(`sha256` de metodo, ruta y cuerpo JSON canonico). Misma clave y cuerpo: se devuelve la respuesta guardada
(`Idempotent-Replay: true`). Otro cuerpo: 422 `codigo: idempotencia`. En vuelo: 409. Solo se guardan 2xx. TTL 7 dias.

## Auditoria

Entidad `ticket`: `create`, `update` (diff por campo del payload con la etiqueta del campo), `status_change`, `decision`
(registrar de nuevo un paso conserva la anterior solo aqui), `comment`, `denied`, y cada disparo de regla.
`GET /tickets/{id}/history/` filtra la bitacora por ticket con el permiso `tickets.view`.

## Puntos de extension (`app/services/ticket_hooks.py`)

- `TICKET_HOOKS[TicketEvent.X]`: corutinas que corren dentro de la transaccion de la peticion tras `created`, `updated`,
  `status_changed`, `decision`, `authorized`, `reading_validated`. Una excepcion revierte todo.
- `CLOSE_CHECKS`: corutinas `(TicketContext) -> str | None`; devolver un mensaje rechaza el cierre (422). El modulo de cierre
  y firma registra aqui la exigencia de soportes y firma.
- `TicketService.register_reading(ticket, fields, source, created_by, attachment_id)`: el modulo de adjuntos/OCR llama a esto
  con el resultado del lector. `TicketRepository.record_*` son los emisores de bitacora reutilizables.
- Columna `ticket_extraction.attachment_id` y propiedad `Ticket.attachments_count` (hoy 0) reservadas: cuando existan los
  adjuntos, la regla `attachments/missing` empieza a leer el conteo real sin cambios de contrato.
- No construido: adjuntos/Blackbox, notificaciones, sellado de firma, conciliacion, pasajeros, bitacora rapida, `totals`.

## Marca, modelo y matrícula de la operación (listas de catálogo)

En el formulario TVOA la fila «Datos de la aeronave» va junto: **Matrícula, Marca, Modelo** (orden de campos del tipo
`operation`: fecha, control, matrícula, marca, modelo, tipo de operación…).

- `aircraft_brand` y `aircraft_model` son `select` de catálogo (`aircraft_brands`, `aircraft_models`), con roles
  `aircraft_brand` / `aircraft_model`, **editables** (ya no `read_only`; antes quedaban vacíos y sin forma de llenarlos).
- Vínculos en el catálogo, en `extra`: `aircraft_models.extra` = código de la marca; `aircrafts.extra` = código del modelo.
- Campo nuevo de definición de campo: `depends_on` (clave de otro `select` del mismo tipo). `aircraft_model.depends_on =
  "aircraft_brand"`: el cliente ofrece solo los items cuyo `extra` es la marca elegida. El constructor valida que el padre
  exista, sea `select` y no sea el propio campo.
- **Coherencia en el servidor** (`app/utils/aircraft_coherence.py`, llamada desde `_checked_payload`): al crear o editar,
  `modelo.extra == marca` y `matrícula.extra == modelo`, con códigos activos. 422 con `errors` por campo:
  `aircraft_model: «El modelo no corresponde a la marca.»`, `aircraft: «La matrícula no corresponde a ese modelo.»`
  (y los espejos `aircraft_brand: «La marca no corresponde al modelo.»`, `aircraft_model: «El modelo no corresponde a la
  matrícula.»` cuando lo escrito ahora es el otro extremo). Marca y modelo siguen siendo opcionales; el servidor **no**
  rellena nada (lo hace el cliente al elegir la matrícula).
- Solo se comprueban los pares que incluyen una clave escrita en esa petición y únicamente si ambos valores son códigos
  conocidos del catálogo: un ticket anterior con texto libre se lee y se edita en otros campos sin revalidar lo que no se toca.
- Bases existentes: `python -m app.scripts.apply_aircraft_catalogs` (idempotente; crea marcas/modelos, enlaza la flota solo
  donde `extra` está vacío y convierte los campos del tipo `operation`).
