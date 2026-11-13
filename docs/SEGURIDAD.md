# Aeronave — cola de seguridad

Norma: `../../docs/seguridad/ESTANDAR.md`. No hay dominio público medido el 12 de noviembre de 2026. No se inventa uno. No publicar.

Nivel: ASVS 5.0.0 L2.

## Lo que el código ya hace

- `docs_enabled` en `app/config/settings.py` deja la documentación apagada fuera de local, salvo `ENABLE_DOCS`.
- `docker/prod/docker-compose.prod.yml` publica solo 80 y 443, en Caddy. La API no tiene puerto de host. El contenedor de Caddy suelta capacidades de más.

## Tareas

### A-01 — Test de documentación apagada

- Estado: `pendiente`
- Capa: código
- ASVS: V4
- Hecho cuando: un test con `ENVIRONMENT=production` y sin `ENABLE_DOCS` espera 404 en `/docs`. Otro con `ENABLE_DOCS=true` demuestra que ese interruptor sigue existiendo para un diagnóstico, y el `env.template` dice que en producción se deja vacío.
- No hacer: no añadir un dominio ni un proxy nuevo.

### A-02 — Semilla de demo fuera de producción

- Estado: `pendiente`
- Capa: código
- ASVS: V6
- Hecho cuando: el arranque con `ENVIRONMENT=production` no crea usuarios de demostración. El test usa una base vacía de prueba, no una copia real.
- No hacer: no imprimir contraseñas de semilla en el assert.

### A-03 — Ficha el día que haya dominio

- Estado: `bloqueada`
- Capa: servidor
- Se desbloquea cuando exista un nombre público. Ahí se aplican las casillas de [ESTANDAR.md](../../docs/seguridad/ESTANDAR.md): naranja, HSTS, `/docs` en 404 y la IP muda.
