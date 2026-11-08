# Limites de uso (rate limit) de los endpoints costosos

Ademas del bloqueo de login, estos endpoints tienen presupuesto propio porque cada llamada cuesta (lectura de
comprobantes con un proveedor de pago, subidas a Blackbox, busqueda sobre datos personales, exportes completos y
lecturas que agregan):

| Accion | Endpoint | Variables (por cuenta / por IP) |
|---|---|---|
| `extract` | `POST /attachments/extract/` | `RATE_LIMIT_EXTRACT_PER_ACCOUNT` (20) / `RATE_LIMIT_EXTRACT_PER_IP` (60) |
| `upload` | `POST /tickets/{id}/attachments/` | `RATE_LIMIT_UPLOAD_PER_ACCOUNT` (60) / `RATE_LIMIT_UPLOAD_PER_IP` (180) |
| `search` | `GET /passengers/search/` | `RATE_LIMIT_SEARCH_PER_ACCOUNT` (60) / `RATE_LIMIT_SEARCH_PER_IP` (180) |
| `export` | `GET /audit/export/`, `GET` y `POST /reconciliation/export/` | `RATE_LIMIT_EXPORT_PER_ACCOUNT` (10) / `RATE_LIMIT_EXPORT_PER_IP` (30) |
| `audit_search` | `GET /audit/` (incluye `search`, ILIKE sobre el diff) | `RATE_LIMIT_AUDIT_SEARCH_PER_ACCOUNT` (60) / `RATE_LIMIT_AUDIT_SEARCH_PER_IP` (180) |
| `heavy_list` | `GET /tickets/`, `GET /tickets/totals/`, `GET /reconciliation/` | `RATE_LIMIT_HEAVY_LIST_PER_ACCOUNT` (240) / `RATE_LIMIT_HEAVY_LIST_PER_IP` (720) |

- Ventana fija `RATE_LIMIT_WINDOW_SECONDS` (60). `RATE_LIMIT_ENABLED=false` lo apaga (solo para pruebas).
- Se consumen **ambos** presupuestos en cada llamada, valida o no, y antes de cualquier trabajo (antes de leer el
  ticket, de tomar bloqueos o de llamar al proveedor). Cambiar de IP no reinicia el presupuesto de la cuenta; muchas
  cuentas detras de una IP (NAT del hangar) comparten el de la IP: por eso el de IP es 3 veces el de cuenta.
- Al agotarse: `429` `{title, detail}` con `Retry-After` (segundos que quedan de la ventana). El detalle es generico.
- Almacen: el mismo que el de login (`app/utils/rate_limit.py`): memoria del proceso, o Redis si hay `REDIS_URL`.
  Con N replicas y memoria el limite real es `limite x N`: Redis es obligatorio al escalar. Si Redis cae se degrada a
  memoria local y se registra (no se abre la puerta).
- La IP es la del socket, o el ultimo salto de `X-Forwarded-For` con `TRUST_FORWARDED_FOR=true` (nunca el primero).
- Pruebas: la suite en proceso sube los presupuestos por entorno (`tests/e2e/conftest.py`); `tests/e2e/test_rate_limits.py`
  arranca un servidor propio con limites reales (`tests/e2e/live_support.py`) y prueba 429, `Retry-After`, cuenta,
  IP, ventana y que nada mas se limita.
