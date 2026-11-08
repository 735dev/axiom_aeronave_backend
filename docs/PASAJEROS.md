# Pasajeros (§6.17)

Manifiesto de pasajeros por operación y búsqueda de "quién viajó dónde". Los datos personales se tratan como
sensibles: el alcance se aplica en la consulta y nunca se guarda la imagen del pasaporte, solo una **referencia**.

## Modelo

`ticket_passenger`: `ticket_id` (FK a la operación), `position`, `full_name`, `document`, `nationality`,
`passport_ref` (UUID del archivo, **sin imagen**), y dos columnas derivadas por el servidor, indexadas:
`name_key` (minúsculas sin tildes ni espacios repetidos) y `document_key` (mayúsculas sin puntos, espacios,
guiones, guiones bajos ni barras). Único por (`ticket_id`, `document_key`). El cliente no puede fijar `id`, `position`
ni las claves: el esquema de entrada solo admite `full_name`, `document`, `nationality`, `passport_file_id`.

## Endpoints

| Endpoint | Permiso | Notas |
|---|---|---|
| `GET /tickets/{id}/passengers/` | `tickets.view` (derivado por tipo) | Manifiesto en orden |
| `PUT` y `POST /tickets/{id}/passengers/` | `tickets.edit` (derivado por tipo) | Reemplaza el manifiesto completo; `POST` es el verbo del simulador |
| `GET /passengers/search/?q=` | autenticado + alcance en la consulta | Máx. 50 personas |

Reglas del reemplazo: solo operaciones raíz (otro tipo = 404); operación cerrada = 409 y evento `denied`; máx. 500
pasajeros; el mismo documento normalizado dos veces = 422 (el manifiesto previo se conserva); nombre y documento
obligatorios; sin permiso de edición del tipo = 403. Cada reemplazo deja un hecho de auditoría.

## Pasaporte

El archivo se sube como adjunto con `field_key=passport` (la operación tiene ese campo `file` sembrado; el tipo de
documento queda `pasaporte`) y el pasajero guarda su `passport_file_id`. **La referencia se valida**: debe ser un
archivo vivo **de esa misma operación** (422 si es de otra, borrado o inexistente). La descarga usa el enlace firmado
de `GET /tickets/{id}/attachments/{file_id}/download/` (permiso y alcance de adjuntos).

## Búsqueda

- Nombre: sin tildes ni mayúsculas, subcadena (`maria jose` encuentra `María José Pérez`).
- Documento: `V-12.345.678`, `v 12 345 678` y `V12345678` son lo mismo. Solo se busca por documento si el texto trae
  un dígito y al menos 3 caracteres, para que `ana` no devuelva el pasaporte `ANA123456`.
- **`LIKE` con escape**: `%` y `_` escritos por el usuario se comparan literalmente. `%`, `_`, `.*`, `(a+)+$`,
  `' OR 1=1` devuelven lista vacía; nada se interpreta como patrón (no hay regex en el servidor).
- Se descartan caracteres de control (un NUL rompía la consulta con un 500; ya no) y la consulta admite 100 caracteres
  (422 si pasa).
- Una misma persona (mismo documento) en varias operaciones es **un** resultado con sus operaciones, la más reciente
  primero, con código, fecha, ruta, estado; y el primer pasaporte enlazado (`passport: {ticket_id, file_id}`).

## Alcance y 404

La consulta solo mira operaciones de los tipos que quien consulta puede leer (`_visible_clause`) y raíz. Si no puede
leer **ninguna** operación, la respuesta es **404** con el mismo cuerpo que un recurso inexistente, nunca 403 ni lista
vacía (un 403 confirmaría que hay datos). Leer gastos u otro tipo no abre operaciones.

## Pendiente conocido

Sin límite de tasa propio en la búsqueda (depende del limitador global); es una búsqueda por texto y se puede
enumerar nombres a ritmo alto con una cuenta válida. Retención y supresión de datos personales (derecho de
cancelación) no están definidas: hoy el manifiesto vive mientras viva la operación.

## Limite de uso y retencion

- `GET /passengers/search/` tiene presupuesto por cuenta y por IP (429 con `Retry-After`; `LIMITES_DE_USO.md`): la busqueda
  recorre datos personales y es la ruta de enumeracion de documentos.
- Retencion: `PASSENGER_RETENTION_DAYS` y `python -m app.scripts.purge_personal_data` anonimizan el manifiesto de operaciones
  cerradas antiguas y retiran sus pasaportes (`RETENCION.md`). Test: `tests/e2e/test_retention_personal_data.py`.
