# Adjuntos de tickets

Los archivos de un ticket (comprobantes, manifiestos, fotos tecnicas, aprobaciones INAC) se **archivan en
Blackbox**; el ticket solo guarda la referencia `{provider, document_id, folder, filename}`. Los bytes no
se guardan en la base ni se devuelven en ninguna respuesta.

## Endpoints

| Metodo y ruta | Permiso | Descripcion |
|---|---|---|
| `POST /tickets/{id}/attachments/` | `tickets.edit` (+ edicion del tipo) | Subida `multipart/form-data`: `file`, `field_key` opcional; cabecera `Idempotency-Key` opcional. 201 con el ticket (`files`). |
| `GET /tickets/{id}/attachments/` | `tickets.view` | Archivos vigentes del ticket. |
| `GET /tickets/{id}/attachments/{file_id}/download/` | `tickets.view` | Enlace firmado `{url, expires_at}` (~5 min, nunca se guarda). |
| `DELETE /tickets/{id}/attachments/{file_id}/` | `tickets.edit` | Quita el archivo mientras el ticket no este cerrado. |
| `POST /tickets/{id}/attachments/{file_id}/validate/` | `tickets.edit` | Valida la lectura del comprobante (compuerta de revision). |
| `GET /attachments/pending/` | `tickets.edit` | Cola de lecturas por validar, acotada en la query a lo que el usuario ve. `limit` maximo **50** (los demas listados admiten 100); por encima: 422 en español (`Revisa los campos marcados: limit.`). |
| `POST /attachments/extract/` | `tickets.create`/`edit` | Lee un comprobante **antes** de crear el registro. No guarda nada. Un tipo inexistente o que el usuario no puede usar responde el mismo **404** (`El tipo de registro no existe.`); el 403 queda solo para la ausencia del permiso de ruta (middleware). |

Fuera del contrato: listar, quitar, `validate` por archivo y `pending`.

## Validacion de la subida (el servidor decide, el cliente solo declara)

Orden: extension -> vacio -> tamano -> firma de contenido.

- Extensiones: `pdf, doc, docx, png, jpg, jpeg` (400 `extension`). Vacio 400 `vacio`. Mas de 20 MB: 413
  `tamano` (tambien por `Content-Length` antes de leer el cuerpo; sin `Content-Length` 411).
- El MIME declarado se **ignora**: los primeros bytes deben coincidir con la extension (422 `tipo`).
  `.docx` se abre como zip y debe contener `word/document.xml`, con limites de entradas y de tamano
  descomprimido (bomba de descompresion).
- Nombre saneado: sin rutas ni separadores, sin caracteres de control ni de reordenado bidireccional,
  acentos fuera, longitud acotada. Nunca se usa el nombre original como ruta.
- Maximo 10 archivos vigentes por registro (422 `limite`). `field_key` debe ser un campo de tipo archivo
  del tipo de ticket (422 `campo`); si se omite se usa el primero.

## Esquema de archivo en Blackbox

Carpeta `AAAA-MATRICULA-NNN` de la operacion (la presenta la API como `Maestro - ...`; sin operacion,
`AAAA-SIN-OPERACION`). Archivo `{ticket}_{tipo}_{nn}_{original}.ext`. `document_type` por campo
(`comprobante`, `manifiesto`, `evidencia_tecnica`, `permiso_inac`, `evidencia_hangar`). La secuencia
`nn` es por ticket y tipo y **no se reutiliza** tras un borrado.

## Subida en tres pasos (Blackbox fuera del bloqueo)

La llamada a Blackbox puede tardar hasta decenas de segundos. Antes se hacia con la fila del ticket bloqueada
(`FOR UPDATE`): cualquier otro cambio al mismo ticket esperaba. Ahora la subida es:

1. **Con el bloqueo** (corto): valida (permiso, ticket abierto, tipo, limite de 10), responde si la clave ya
   termino (repeticion), **reserva un cupo** (`attachment_upload`: ordinal del nombre, clave determinista para
   Blackbox, hash) y hace `COMMIT`, que libera el bloqueo. Es el unico commit a mitad de peticion del codigo
   (`AttachmentRepository.checkpoint`).
2. **Sin bloqueo**: archiva en Blackbox con la clave de cliente del cupo (con `Idempotency-Key` es determinista:
   un reintento tras una respuesta perdida recibe `already_stored` y el mismo documento).
3. **Con el bloqueo otra vez** (`open_for_change(..., fresh=True)` relee la fila): revalida (el ticket pudo cerrarse
   mientras viajaba el archivo), crea el adjunto y borra el cupo **en la misma transaccion**.

| Que pasa | Resultado |
|---|---|
| Blackbox rechaza o esta caido (503 `almacenamiento`) | Se borra el cupo (compensacion en commit propio); nada a medias; el ordinal se libera |
| El cliente se desconecta / el proceso cae con el archivo ya en Blackbox | No hay adjunto ni evento. El cupo queda. El reintento con la **misma clave** lo retoma (mismo ordinal), Blackbox contesta `already_stored` y se enlaza **una sola vez** |
| Lo mismo sin `Idempotency-Key` | Queda un archivo en Blackbox sin referencia (su API no borra); el cupo se limpia con `app.scripts.sweep_uploads` |
| Dos peticiones simultaneas con la misma clave | Un solo adjunto; la segunda responde como repeticion (`Idempotent-Replay: true`) |
| Dos subidas simultaneas distintas | Ordinales distintos (`_01_`, `_02_`): el cupo reserva el numero bajo el bloqueo |
| El ticket se cierra o anula mientras se archiva | 409 y el intento se audita `denied`; no se enlaza (el archivo ya archivado queda sin referencia, ventana pequeña) |
| Cupos abiertos | Cuentan para el limite de 10 mientras viven |

Pruebas: `tests/e2e/test_attachments_lock.py` usa el fake de Blackbox con fallos inyectados por nombre de archivo
(`bbslow` = guarda y tarda ~2 s en contestar, `bbdown`, `bblose`...). Comprueba que otro cambio al ticket no espera, que
cortar la peticion a mitad deja todo limpio y que el reintento termina una sola vez.

## Idempotencia y fallos

- `Idempotency-Key` se resuelve en el servicio por (ticket, usuario, clave) con comparacion del hash del
  contenido: repeticion igual = misma respuesta con `Idempotent-Replay: true`; mismo valor con otro
  contenido = 422 `idempotencia`. El `IdempotencyMiddleware` **no** aplica a esta ruta (multipart de hasta
  20 MB, el limite cambia en cada reintento).
- Fallo de Blackbox: 503 `almacenamiento`, sin registro a medias (ver arriba); los transitorios se reintentan
  con la misma clave.
- Ticket cerrado: 409 y el intento se audita como `denied`.
- Limite de uso: 429 con `Retry-After` (ver `LIMITES_DE_USO.md`), tambien en `extract`.

## Lectura de comprobantes

Una imagen PNG/JPG en un tipo con rol `amount` se lee con el proveedor elegido por `RECEIPT_READER`
(`fake` por defecto, `anthropic`, o apagado). El servidor valida cada valor propuesto (se descartan los
invalidos) y guarda una lectura `extracted` o `unreadable` via `TicketService.register_reading`. Un
fallo del proveedor nunca bloquea la subida. Ver `LECTURA_COMPROBANTES.md`.

## Auditoria y reglas

Subir = accion `attach`; quitar = `delete`. `Ticket.attachments_count` es real, y las reglas se
reevaluan al subir y al quitar (la de "todo gasto debe tener comprobante" aparece y desaparece).
