# Lectura automática de comprobantes (§6.14)

Módulo: `app/integrations/receipt_reader/`. Lee un comprobante (ticket térmico de combustible,
factura, recibo) y **propone** monto, moneda, fecha, proveedor y concepto, cada uno con
confianza `high` o `low`. Es una propuesta: nada leído por máquina se acepta sin validación humana.

> **Estado de la precisión: NO medida.** Todavía no se ha medido el acierto sobre comprobantes
> reales de Transvalor. Los tests verifican el contrato, la validación y el manejo de fallos con
> respuestas simuladas, no la calidad de lectura. Debe medirse antes de cerrar la fase (ver
> "Protocolo de medición").

## Piezas

| Archivo | Rol |
|---|---|
| `types.py` | `ReadField(key, value, confidence)` y `ReceiptReading(status, fields, provider)` inmutables. `status` es `extracted` o `unreadable`. |
| `base.py` | Protocolo `ReceiptReader`, `ReadHints` y guardas de imagen. |
| `anthropic_reader.py` | Adaptador real: `POST /v1/messages` con una imagen y una instrucción estricta. |
| `parsing.py` | Extrae el JSON de la respuesta y valida cada valor en el servidor. |
| `fake_reader.py` | Lector determinista (hash de los bytes, ~1 de cada 7 ilegible) para tests y desarrollo. |
| `service.py` | `ReceiptReadingService`: reintentos, degradación a `unreadable` y `confidence_gate`. |

`ReadHints` lleva las monedas permitidas, los códigos del catálogo de conceptos de gasto y las
claves de campo del tipo de ticket por rol (`amount_key`, `currency_key`, `item_key`,
`accounting_date_key`, `supplier_key`). La salida usa esas claves, así que encaja directo con el
formulario del tipo de ticket. Siempre se devuelven los cinco campos; el que no se pudo leer
va con `value=None` y `confidence="low"`.

## Reglas de seguridad

1. **El texto de la imagen es dato no confiable.** La instrucción ordena ignorar cualquier
   instrucción dentro de la imagen. No se confía en eso: lo que protege es la validación.
2. **Validación de cada valor en el servidor.** Lo inválido se descarta (no se adivina):
   - monto: decimal finito, positivo, ≤ 10.000.000, normalizado a 2 decimales;
   - moneda: debe estar en `hints.currencies`;
   - fecha: `YYYY-MM-DD` válida, no futura, no anterior a ~5 años;
   - concepto: debe estar en el catálogo, coincidencia exacta;
   - proveedor: recortado, ≤ 120 caracteres, sin caracteres de control;
   - claves desconocidas o estructuras anidadas se ignoran.
3. Un dato que el modelo marcó nulo o `low` queda en `low`. Falta de confianza informada = `low`.
   Si `legible` es falso o no sobrevive ningún valor, el resultado es `unreadable`.
4. **Falla cerrado.** Errores HTTP, timeouts, redes caídas y respuestas mal formadas levantan
   `ReceiptReaderError(transient)` con mensaje fijo (sin eco de cuerpos ni claves). El servicio
   los convierte en `unreadable` y los registra; nunca se lo propaga al usuario.
5. **Imagen:** solo png/jpeg/webp/gif, no vacía, ≤ 5 MB (5 MB exactos pasan, +1 byte no).
   Se rechaza antes de cualquier llamada de red.
6. **Transporte:** `base_url` https (http solo para localhost), sin credenciales embebidas,
   `follow_redirects=False`, timeout explícito (30 s), respuesta acotada a 1 MB.
7. **La API key nunca sale del servidor:** no aparece en `repr`/`str`, logs ni errores.
   Los logs solo llevan el código de estado y el tipo de excepción.

## Variables de entorno

| Variable | Uso |
|---|---|
| `ANTHROPIC_API_KEY` | Clave de la API. **Solo servidor.** Nunca al frontend, nunca al repo. Gestor de secretos en despliegue. |
| `RECEIPT_READER_MODEL` | Modelo (por defecto `claude-sonnet-5-5`). |

Sin `ANTHROPIC_API_KEY` el cableado debe usar `FakeReceiptReader` solo en desarrollo; en
producción, sin clave, la lectura debe quedar deshabilitada (carga manual), no caer al fake.

## Cómo se conecta

El módulo no expone HTTP; lo consume la capa de endpoints:

- **`POST /attachments/extract/`**: valida permisos y pertenencia del ticket/tipo (scoping por
  tenant, 404 si es ajeno), arma `ReadHints` desde el tipo de ticket (campos por rol, monedas,
  catálogo de conceptos) y llama `ReceiptReadingService.read(image, mime, hints)`. Responde
  `status`, `fields` y `review_required = confidence_gate(reading)`. Un `unreadable` es una
  respuesta 200 normal: el usuario sigue con carga manual.
- **Subida de adjunto**: la imagen original se archiva siempre en Blackbox
  (`AttachmentVault`), con o sin lectura. La lectura es un efecto adicional que nunca bloquea
  ni deshace la subida.

## Compuerta de validación humana

- Todo lo leído llega como propuesta; el frontend exige confirmación humana antes de aceptarlo.
- `confidence_gate(reading)` devuelve las claves que requieren **revisión obligatoria**:
  confianza `low` o sin valor. Con todo en `high` se propone y espera confirmación.
- El backend no debe guardar un valor leído como dato definitivo sin la confirmación del usuario,
  y debe registrar quién confirmó/corrigió y qué propuso la máquina.

## Protocolo de medición (pendiente, obligatorio antes de cerrar la fase)

1. **Muestra:** ≥ 200 comprobantes reales de Transvalor, estratificados por proveedor, tipo
   (combustible térmico, hotel, taxi, etc.) y estado del papel (nítido, decolorado, arrugado).
   Incluir ≥ 20 ilegibles a propósito. Tratar los datos como confidenciales.
2. **Verdad de terreno:** dos personas transcriben cada campo; las discrepancias las resuelve un
   tercero. Se guarda por separado de la salida del lector.
3. **Medición por campo** (monto, moneda, fecha, proveedor, concepto): tasa de coincidencia
   **exacta** (monto al centavo, fecha exacta, proveedor normalizado en mayúsculas y sin
   puntuación). Reportar además: tasa de `unreadable`, y la calibración de confianza, es decir
   el acierto dentro de `high` frente al de `low`.
4. **Métrica clave:** errores en `high` (dato equivocado con confianza alta). Es el riesgo real,
   porque el usuario tiende a aceptarlo sin revisar.
5. **Umbrales a acordar con el cliente (propuesta inicial, no acordada):** monto y moneda
   ≥ 95 % exacto en `high`; fecha ≥ 90 %; proveedor y concepto ≥ 80 %; tasa de error en `high`
   ≤ 2 % por campo; ilegibles degradan a manual sin excepciones.
6. Si no se alcanzan: ajustar instrucción/modelo/preprocesado de imagen y volver a medir con una
   muestra nueva (no la de ajuste). Presentar el informe al cliente antes del cierre.
