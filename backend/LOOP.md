# LOOP.md: memoria del loop de implementación

Archivo de estado del loop que implementa el backend Trust spec por spec. Cada iteración lo lee primero y lo actualiza antes de su commit.

## Instrucciones del loop (copia limpia)

El texto original del `/loop` llegó con cortes de la terminal; esta es la versión que manda.

- **Meta**: backend Trust (FastAPI) 100 % implementado, probado y funcionando: todas las specs de `backend/specs/`, todos los endpoints (con ★ y sin ★) y todos los AC. Sin preguntar al usuario: decidir con los defaults de las specs y anotar cada decisión aquí.
- **Fuentes de verdad**: `backend/specs/` (comportamiento) · `contracts/trust-api.openapi.yaml` (rutas, campos, códigos; el frontend ya lo usa) · `contracts/op-service.openapi.yaml` (servicio Go; **nunca** se modifica). Sin Go ni frontend: todo lo de Go va por FakeOpClient (`OP_CLIENT=fake`). Asset de mercado COP escala 2. Recaudo de Trust con grant permanente; el único link de autorización es el del desembolso.
- **Cada iteración**:
  1. `git status`, rama `greenfield/backend`, leer este archivo; terminar trabajo a medias.
  2. Tomar la primera spec del README que no esté ☑ (si hay una ◐, esa).
  3. Implementarla con un workflow pequeño: implementador (código + un test por AC con `assert_contract`, pytest verde) → revisor adversarial (spec y YAML) → fixer si hace falta.
  4. Verificar: pytest completo verde + `ruff check`; quitar skips que dependían de esta spec.
  5. Marcar ☑ (Dueño: Claude), actualizar este archivo, un commit atómico "Implementar spec NN · módulo" con las líneas `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` y `Claude-Session: https://claude.ai/code/session_01PAVJYMSHPqvY1AxEqB9eEh`. Sin push hasta la fase final. No tocar `landing/`, `.internal/`, `CLAUDE.md`, `contracts/op-service.openapi.yaml`. No commitear `.env`.
  6. Siguiente iteración (60 s si sigue de inmediato; respaldo largo mientras corre un workflow).
- **Reglas**: la spec manda (si es ambigua, se corrige mínimo y se anota en "Decisiones") · el YAML de la Trust API solo cambia si es inevitable (spec + YAML juntos, `npx --yes @redocly/cli@1.34.0 lint`, anotar en "Cambios de contrato") · dinero en enteros y bps · nunca llamar a op-service con una transacción abierta · tests contra Postgres real · al final no puede quedar ningún bloqueo, skip ni AC sin implementar.
- **Fase final** (17 specs ☑, sin skips): smoke E2E `backend/scripts/smoke_e2e.py` (demo completa validada contra el YAML) → mock HTTP de op-service en `backend/tools/mock_op_service/` y misma prueba con `OP_CLIENT=http` → Dockerfile, docker-compose (api + postgres), `.env.example`, `backend/README.md` → revisión final adversarial → commit de cierre → `git fetch`, `git push -u origin greenfield/backend`, `git checkout main`, `git merge origin/main`, `git merge --no-ff greenfield/backend` (resolver conflictos sin perder trabajo de nadie; nunca borrar lo de la landing) → pytest + ruff + smoke sobre main → `git push origin main` (si lo rechazan, repetir; nunca `--force`) → detener el loop y reportar.

## Entorno

- Docker vía OrbStack (`orb start` si el daemon no responde).
- Postgres 16 en Docker: `cd backend && docker compose up -d db` → `localhost:5433`, usuario/clave `trust`/`trust`, bases `trust_dev` y `trust_test`.
  - `DATABASE_URL=postgresql+psycopg://trust:trust@localhost:5433/trust_dev`
  - `TEST_DATABASE_URL=postgresql+psycopg://trust:trust@localhost:5433/trust_test`
- Python 3.12 con `uv` (`backend/pyproject.toml`). Tests: `cd backend && uv run pytest`. Lint: `uv run ruff check .`

## Estado

| Spec | Estado | Commit |
|---|---|---|
| 00-core | ☑ | ver log |
| 01-data-model | ☑ | ver log |
| 02-op-client | ◐ | — |
| 03 → 16 | ☐ | — |

## Decisiones

- AC-6 de 00-core (rutas de la app = rutas del YAML): hasta que existan todos los endpoints se prueban dos cosas: toda ruta de la app existe en el YAML con los mismos nombres de parámetros, y `assert_contract` valida status y body. La igualdad completa (todas las rutas del YAML implementadas) es un test aparte con skip, que se activa en la fase final.

- Config: `DATABASE_URL` y `JWT_SECRET` obligatorios (`JWT_SECRET` ≥ 32 caracteres, falla al arrancar). Listas de env separadas por coma. Defaults vacíos (`""` o `[]`) para `CORS_ORIGINS`, `PUNTORED_WEBHOOK_SECRETS`, `OP_SERVICE_*` y `TRUST_REVENUE_WALLET_ADDRESS`.
- Money: `Money.from_api` y `MoneySchema` (Pydantic estricto) rechazan ints, floats, bools, negativos y ceros a la izquierda; el chequeo de asset de mercado es `require_market_asset()` (422 `asset_mismatch`). `clock.UtcDateTime` serializa siempre con `Z`.
- JWT: `exp` se valida contra `clock.now()` (así el reloj congelado de los tests sirve).
- Errores: 404 → `not_found`; 405 y otros 4xx del framework → `bad_request`; 500 → `internal_error` (con CORS). `validation_error.details = {"fields": [...]}`.
- `op_cursors` vive en `app/modules/worker/models.py`; `loan_pct_changes` no tiene columnas de asset (sus Money usan el asset del crédito).
- CHECK extra derivados de 01: `business_name` obligatorio para merchant; `score` entre 0 y 1000.
- Helpers compartidos: `deps.Pagination`, `next_offset`, `parse_id` (id no UUID → 404) y `users/schemas.py` (`MerchantRef`, `LenderRef`).
- Tests: un `create_app()` aparte con rutas `/api/v1/_test/*` para probar auth y validación sin ensuciar el contrato; esquema recreado por sesión y `TRUNCATE` por test; bcrypt con 4 rondas en tests.
- Dependencia `httpx2` (dev): legítima (Tom Christie, github.com/pydantic/httpx2); el TestClient de Starlette 1.8 la exige.
- `trust_dev` ya tiene tablas creadas por `create_all`: si una spec agrega columnas, hay que recrear esa base (`DROP SCHEMA public CASCADE; CREATE SCHEMA public;`).

## Cambios de contrato

(ninguno)

## Bloqueos

(ninguno)

## Log

- 2026-10-17 01:45 · Iteración 1: Docker (OrbStack) arriba, Postgres en `backend/docker-compose.yml`, bases creadas, LOOP.md creado. Empiezan 00-core + 01-data-model.
- 2026-10-17 02:40 · Specs 00-core y 01-data-model ☑: proyecto uv, core, 15 tablas, arnés de tests con `assert_contract`. 14 tests pasan, 1 skip planificado (rutas completas, fase final). Revisor encontró 8 fallas; todas corregidas.
