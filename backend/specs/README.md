# Specs del backend Trust

Construimos el backend **spec-driven, en versión ligera**: una spec corta por módulo. Se implementa la spec, sus criterios de aceptación se vuelven tests, y se marca como hecha. Nada más.

- Diseño general (el porqué): [`../docs/diseno-backend.md`](../docs/diseno-backend.md)
- Contrato con op-service (Go): [`../../contracts/op-service.openapi.yaml`](../../contracts/op-service.openapi.yaml)
- Contrato HTTP para web y móvil (fuente de verdad de las formas): [`../../contracts/trust-api.openapi.yaml`](../../contracts/trust-api.openapi.yaml)
- Pantallas que debe alimentar la API: [`../../landing/docs/COPY.md`](../../landing/docs/COPY.md) §13

## Reglas del juego

1. **La spec manda.** Si al implementar algo no cuadra, primero se corrige la spec (2 líneas bastan) y después el código. Nunca al revés.
2. **Dos specs base, siempre cargadas**: [`00-core.md`](00-core.md) (convenciones) y [`01-data-model.md`](01-data-model.md) (todas las tablas). Una spec de módulo **no redefine** tablas, enums, formato de dinero ni errores: los referencia.
3. **El YAML del contrato también manda.** Cada endpoint se implementa con las rutas, campos y códigos exactos de `contracts/trust-api.openapi.yaml`, y sus tests validan las respuestas con `assert_contract` (00-core AC-6).
4. **Cada criterio de aceptación (`AC-n`) es un test** en `backend/tests/test_<modulo>.py` con nombre `test_ac<n>_<descripcion_corta>`. Si no tiene test, no está hecho.
5. **★ = MVP de la demo.** Lo que no tiene ★ se hace solo si sobra tiempo.
6. **Estado** en la cabecera de cada spec: `☐ pendiente` → `◐ en curso` → `☑ hecho`. Quien la toma pone su nombre en `Dueño`.
7. Specs cortas: si una pasa de ~150 líneas, se parte.

## Flujo por spec (≈ 30–90 min cada una)

1. Tomar la siguiente spec del orden de abajo y marcarla `◐` con tu nombre.
2. Implementar en `backend/app/...` siguiendo la estructura de `00-core.md`.
3. Escribir un test por cada `AC-n` y correr `pytest`.
4. Marcar `☑`. Si cambió algo, actualizar la spec en el mismo cambio.

Prompt sugerido para la IA:

> Implementa `backend/specs/NN-modulo.md`. Respeta `backend/specs/00-core.md` y `backend/specs/01-data-model.md`. Escribe un test por cada AC en `backend/tests/test_modulo.py`. No cambies otras specs; si algo no cuadra, dímelo.

## Orden de construcción

| Fase | Spec | Qué deja funcionando |
|---|---|---|
| 0 · Base | [00-core](00-core.md) · [01-data-model](01-data-model.md) · [02-op-client](02-op-client.md) · [03-platform-dev](03-platform-dev.md) · [04-users](04-users.md) | Proyecto que arranca, tablas, cliente Go falso, seed, login |
| 1 · Flujo de la plata | [05-policies](05-policies.md) · [06-recaudo](06-recaudo.md) · [07-sales](07-sales.md) · [08-worker](08-worker.md) | Venta simulada → webhook → división en 3 → transferencias |
| 2 · Crédito | [09-scoring](09-scoring.md) · [10-marketplace](10-marketplace.md) · [11-loans](11-loans.md) | Score y cupo, ofertas y solicitudes, aceptar y desembolsar |
| 3 · Pantallas | [12-repayments](12-repayments.md) · [13-dashboard](13-dashboard.md) · [14-notifications](14-notifications.md) · [15-waitlist](15-waitlist.md) · [16-admin](16-admin.md) | Lo que muestran la app y la web |

Las fases 1 y 2 pueden ir en paralelo si hay dos personas: comparten solo `05-policies` y `01-data-model`.

## Plantilla

Nuevas specs: copiar [`_template.md`](_template.md).
