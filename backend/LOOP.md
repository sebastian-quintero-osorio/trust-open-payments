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
- Compose publica Postgres solo en `127.0.0.1:5433` (desde la revisión final). Login del admin del seed: `DEMO_ADMIN_PASSWORD` en `.env` (≥ 12 caracteres, distinta de `DEMO_PASSWORD`).
- Ramas: `greenfield/backend` ya está mergeada a main y borrada. Por pedido del usuario, los arreglos posteriores van en `bugfix/<área>` creada desde main → merge `--no-ff` a main → se borra la rama.

## Estado

| Spec | Estado | Commit |
|---|---|---|
| 00-core | ☑ | ver log |
| 01-data-model | ☑ | ver log |
| 02-op-client | ☑ | ver log |
| 03-platform-dev | ☑ | ver log |
| 04-users | ☑ | ver log |
| 05-policies | ☑ | ver log |
| 06-recaudo | ☑ | ver log |
| 07-sales | ☑ | ver log |
| 08-worker | ☑ | ver log |
| 09-scoring | ☑ | ver log |
| 10-marketplace | ☑ | ver log |
| 11-loans | ☑ | ver log |
| 12-repayments | ☑ | ver log |
| 13-dashboard | ☑ | ver log |
| 14-notifications | ☑ | ver log |
| 15-waitlist | ☑ | ver log |
| 16-admin | ☑ | ver log |

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

- (02) set_wallet con defaults (sin asset = asset de mercado); fail_payment agregado para probar desembolsos fallidos; set_op_client() para que tests, rutas, lifespan y worker usen la misma instancia.
- (02) Al arrancar con el fake se adoptan las cuentas de recaudo y el seq del simulador continúa sobre el máximo BREB-SIM guardado (evita referencias duplicadas tras reinicio).
- (02) HttpOpClient convierte cualquier httpx.HTTPError (incl. DecodingError) en OpUnavailable.

- (03) El seed agrega historial solo a negocios con recaudo activo y sin ventas seed; reejecutar no duplica. La calibración del ≈ 58 % / 12 % / 32 % pagado asume que el crédito se siembra el mismo día que el historial.
- (03) Simular venta: orden de validación id → cuenta (404/409, revocada → 409 sin llamar a Go) → asset (422) → valor > 0 (422) → Go.
- (03) ~~`POST /dev/seed` con cuerpo malformado da 422 `validation_error`~~ (reemplazada en la revisión final: el cuerpo se lee tolerante y da 200).
- (04) PATCH /me ignora `business_name` y `email`; `lender_range` de un no prestamista → 422 (aunque sea null). Cambio de wallet deja evento `payout_wallet_set`. Login con email inexistente corre bcrypt sobre un hash dummy (mismo tiempo que contraseña errada).
- (00) El fixture `client` limpia `fake_op.calls` después del arranque, porque el arranque ahora llama `resolve_wallet_address` para verificar el asset.
- (06) Un `409 pool_exhausted` de Go deja un evento `user` `collection_account_pool_exhausted` (`{"idempotency_key"}`) y el siguiente intento usa la clave `merchant:{id}:recaudo:{n+1}` con `n` = revocadas + esos eventos, porque op-service guarda 72 h la 409 definitiva por clave (si no, el negocio no conseguiría cuenta aunque el pool se rellene).

- (05) Terms.to_dict() devuelve montos enteros (centavos); convertir a Money es de 11. features/unlocks_at en la zona de cfg.timezone; razones en singular cuando aplica.

- (07) Pata Trust con TRUST_REVENUE_WALLET_ADDRESS vacío: queda held con receptor null y log de error; 16 la llena al reintentar (o 409 invalid_state si sigue vacío).
- (07) next_since nunca retrocede (máximo entre el último created_at leído y el since recibido), con microsegundos y Z; since sin zona → 422. GET /sales: fecha malformada o from > to → 422; orden paid_at desc, id asc.
- (07) sale_split solo para ventas processed (no held), como dice 14.
- (07) Webhook: JSON con NaN/Infinity, \u0000 o montos fuera de bigint → 400 (antes daban 500 y Go reintentaría para siempre). bre_b_reference vacío u occurred_at sin zona → unmatched (invalid_payload).

- (08) Reintento con la misma clave ante cualquier OpError `retryable` o 401 (como Go caído). `get_transfer` con 404 → pata `failed_final` (`not_found`) + `payout_failed`; 401/retryable → siguiente tick. Reintento sobre cuenta revocada → pata `held`.
- (08) Contabilidad de entrega: lo entregado por intento se acota a [0, current_debit_amount]; `receive_*` acumula si el asset no cambia; fallo sin resto → `completed` (y `abono_received`).
- (08) Eventos de patas: `processing`, `completed`, `retry_scheduled`, `held`, `failed_final`; cuenta revocada por el worker: `revoked` con `{reason, source: "worker"}`.
- (14) `render()` omite el `.` del texto que sigue a un valor que ya termina en `.` (p. ej. "10:42 a. m.").

- (09) get_credit_profile hace commit al insertar un assessment: llamarlo antes de tomar locks (11 R6). Solo cuentan ventas con paid_at ≤ now en el asset de mercado; months_on_platform cuenta meses completos como edad.

- (10) scope=market con status distinto de open → lista vacía; admin en /matches → 403; los endpoints rechazan rol incorrecto (403) antes de buscar el id (404).
- (10) Dinero de entrada por encima de BIGINT_MAX (core/money.py) → 422 validation_error en toda la API; offset de paginación acotado a bigint.

- (11) Orden de validación R4: fee_bps/term_months del prestamista sin oferta se valida tras asset_mismatch y antes de los montos. En match, fits() se revisa aunque venga amount (oferta con % mayor → no_overlap).
- (11) Desembolso: OpError retryable o 401 → 503 op_unavailable con el mismo intento; 201 sin authorization_url → queda disbursing y la API responde 409 invalid_state. authorization_url/expires_at solo para el prestamista. Aceptar con expires_at vencido → 409 invalid_state.
- (11) close_period lanza ValueError si el crédito no existe o no está active (dev → 404/409). Id no UUID en el body → 422; status desconocido en GET /loans → 422.

- (12) `clock.local_day_bounds(date.max)` termina en `datetime.max` UTC: `to=9999-12-31` (fecha válida) ya no da 500; arregla también `GET /sales` (07).
- (07/12) `from`/`to` de `GET /sales` y `GET /repayments` solo aceptan `YYYY-MM-DD` (`clock.LocalDate`): timestamps Unix o fechas con hora → 422 `validation_error` (el modo laxo de Pydantic los aceptaba).

- (12) Filtro loan_id no UUID → 422 (las ids de ruta dan 404); empates de paid_at por id asc; delivery.received sale de receive_* de la pata lender aunque no esté completed; traza en orden lender, merchant, trust.

- (13) offers_fit_count y el count de GET /loan-offers?scope=market salen de la misma función; totals pone primero el asset de mercado y otros assets solo con datos; abonos_por_hora es GROUP BY de la hora local de la venta.

- (14) Lista ordenada created_at desc, empates por id desc; el enum de type sale del catálogo.

- (15) Content-Type distinto de urlencoded se lee como JSON; honeypot tras el rate limit; 429 JSON con Retry-After; NUL o JSON muy anidado → 422/303 error (antes 500).

- (16) Reintento guarda la wallet completada en receiver_wallet_url; pata lender sin receptor → 409 invalid_state. Revocación bloquea patas en el mismo orden que el worker (sin deadlocks).

- (final) Smoke E2E `scripts/smoke_e2e.py`: recrea `trust_smoke`, arranca uvicorn en un cwd temporal (sin `.env`) con el worker cada 1 s y valida cada respuesta con `assert_contract` (29 pasos). `--with-mock` (o `scripts/smoke_http.sh`) arranca antes el mock en un puerto libre con API key y secreto aleatorios, corre con `OP_CLIENT=http`, aprueba en `/mock/consent/{id}` y en el paso 30 comprueba en el mock que los 5 webhooks quedaron entregados (si la firma fallara, las ventas llegarían por reconciliación y el paso falla).
- (final) Mock de op-service `tools/mock_op_service/` (en memoria): el simulador completa en el mismo POST y envía el webhook desde un hilo (reintentos 1, 5, 30, 120 s; un replay no lo reenvía); transferencias y pagos aprobados quedan `completed` en el siguiente GET tras `MOCK_SETTLE_SECONDS` (0); consentimiento en `{PUBLIC_BASE_URL}/mock/consent/{id}?decision=approve|reject` → 302 al `return_url` conservando su query; sin comisiones ni tasa de cambio; toda wallet resuelve COP/2 salvo `/mock/wallets`; reiniciarlo pierde todo (recrear `trust_dev` o `MOCK_SIM_SEQ_START`); sin `/v1/admin/pool-wallets*` ni `/qr/{id}`; controles `/mock/*` fuera del contrato.
- (final) Docker: `python:3.12-slim` + uv 0.12.19, `uv sync --locked --no-dev`, usuario uid 10001, `HEALTHCHECK` con urllib (503 → unhealthy). En compose la API usa `db:5432` (`TRUST_API_DATABASE_URL` para otra base) y publica `${TRUST_API_PORT:-8000}`; `.env` es opcional (`required: false`) para que `docker compose up -d db` no dependa de él; servicio Caddy comentado.
- (revisión final) Texto de entrada sin `\u0000` (`core/text.py`): `first_name`, `last_name`, `business_name` ≤ 120 y `city` ≤ 80 en register y PATCH /me → 422 `validation_error`; `wallet_address` con NUL o > 2048 → 422 `invalid_url` sin llamar a Go (antes, 500 de psycopg).
- (revisión final) `clock.UtcDateTime` convierte el `OverflowError` de `astimezone(UTC)` en `ValueError`: `since=0001-01-01T00:00:00+14:00` o `9999-12-31T23:59:59-14:00` → 422 (el router usa `UtcDateTime` en vez de `AwareDatetime`) y un webhook con ese `occurred_at` → `unmatched`/`invalid_payload`.
- (revisión final) `MoneySchema.value` y `Money.from_api` rechazan más de 19 dígitos ("El monto es demasiado grande.", 422): `int()` fallaba sobre 4300 dígitos. El webhook con un monto así → `unmatched`/`invalid_payload`. `/dev/sales/simulate` valida `value ≤ BIGINT_MAX` antes de llamar a Go.
- (revisión final) Webhook: JSON anidado a más de 64 niveles o `RecursionError` al leerlo → 400 `bad_request` (chequeo de profundidad iterativo antes de `_has_nul`/`_strip_nul`/JSONB, que son recursivos).
- (revisión final) `create_engine(hide_parameters=True)`: el log del 500 no lleva valores enlazados (hashes, correos, wallets).
- (revisión final) `DevGuardMiddleware` (ASGI, dentro de CORS): con `/dev` apagado, `/api/v1/dev/*` es 404 antes de leer el cuerpo. `POST /dev/seed` lee el cuerpo a mano y tolera cualquier cosa (vacío, ilegible o no objeto → sin opciones; el flag solo cuenta si es `true`): el YAML de `devSeed` no declara 422 y así no hizo falta cambiarlo (reemplaza la decisión (03) de dejar el 422).
- (revisión final) `admin@demo.trust` ya no usa `DEMO_PASSWORD` (pública y devuelta por `/dev/seed`): usa `DEMO_ADMIN_PASSWORD` (sin default; ≥ 12 caracteres y distinta de `DEMO_PASSWORD`/`trust2026` o la app no arranca). Vacía → contraseña aleatoria; el seed sincroniza la contraseña en cada corrida (un admin viejo con `DEMO_PASSWORD` la pierde) y no lista al admin en `users`. El smoke define una aleatoria y entra con ella.
- (revisión final) `TRUSTED_PROXIES` (IPs/CIDR, vacío por defecto): `X-Forwarded-For` solo se cree si la conexión viene de ahí (con el Caddy de compose, `172.16.0.0/12`); el limitador es LRU con tope de 10.000 IPs. Los tests de la lista de espera ponen `testclient` como proxy.
- (revisión final) `BodySizeLimitMiddleware`: más de 1 MiB (64 KiB en webhook y lista de espera) → 413 `payload_too_large` sin leer el cuerpo si viene `Content-Length`; sin él (chunked), al pasar el tope la app ve una desconexión y su respuesta se cambia por el 413 (`ClientDisconnect` ya no deja un traceback de 500 en el log). Es un tope de transporte (como el de un proxy): no se agregó 413 a cada operación del YAML.
- (revisión final) docker-compose: Postgres solo en `127.0.0.1:5433` y `POSTGRES_PASSWORD` del entorno (default `trust`, también en el `DATABASE_URL` de la API). El contenedor `backend-db-1` que ya corre conserva el puerto viejo hasta recrearlo (`docker compose up -d db`; el volumen se mantiene).
- (revisión final) 07 R3 6b: `trust_pending` se queda en la cuenta donde se acumuló (la de la venta del último abono con `trust_cut > 0`, mayor `allocated_after`; sin columna nueva). Si llega una venta por otra cuenta, lo pendiente sale antes como pata `trust` de esa venta anterior (`held` si su cuenta o la venta no están activas) y vuelve a 0. Invariante por cuenta: Σ patas + pendiente acumulado = `ledger_in`.
- (revisión final) 08 R4: envío ambiguo = pata `pending` con evento `submit_unknown` `{attempt}` (se registra al diferir por OpUnavailable/401/retryable/`request_in_progress`, uno por intento; sin columna nueva). Las revocaciones (J1 409, J2 `grant_revoked`, 16 R6 vía `pending_legs_to_hold`) no la retienen: J1 repite la clave; 201 → `processing`, 409 `collection_account_not_active` → `held`. En J1, la pata que recibe la 409 se retiene siempre. Si la revocación en Go falló y Go nunca había creado esa clave, el reintento la crea: la plata se mueve pero queda contabilizada.
- (revisión final) 08 R8: J5 confirma cada item en su propia transacción (sin deadlock con un webhook en vivo del mismo evento) y el cursor solo avanza por el tramo inicial de items terminados sin error; un item que lanzó o que sigue `processing` se repite en la próxima reconciliación.
- (revisión final) Sin cambiar: rate limit de `POST /auth/login` (el YAML de `login` no declara 429, 04 no lo pide y la contraseña del admin ya es secreta; se recomienda limitarlo en Caddy).

## Cambios de contrato

(ninguno)

## Pendientes por dependencia (cerrar antes de la fase final)

- ~~03 AC-6~~ cerrado con la spec 06.
- ~~03 AC-8~~ cerrado con la spec 07.
- ~~03 AC-11 y seed de créditos R9/R10~~ cerrados con la spec 11.
- ~~08 AC-9 y AC-10~~ cerrados con la spec 11.
- ~~00-core `test_ac6_all_contract_routes_implemented`~~ activo desde la spec 16.

## Bloqueos

(ninguno)

## Log

- 2026-10-17 01:45 · Iteración 1: Docker (OrbStack) arriba, Postgres en `backend/docker-compose.yml`, bases creadas, LOOP.md creado. Empiezan 00-core + 01-data-model.
- 2026-10-17 02:40 · Specs 00-core y 01-data-model ☑: proyecto uv, core, 15 tablas, arnés de tests con `assert_contract`. 14 tests pasan, 1 skip planificado (rutas completas, fase final). Revisor encontró 8 fallas; todas corregidas.
- 2026-10-17 02:47 · Spec 02-op-client ☑: OpClient Protocol, HttpOpClient (según op-service.openapi.yaml) y FakeOpClient con controles. 48 tests pasan. El revisor encontró 3 fallas; corregidas.
- 2026-10-17 03:32 · Spec 04-users ☑ y spec 03-platform-dev parcial (health completo con verificación del asset de mercado, config, guardas dev, wallet falsa, seed de usuarios/ofertas/historial, simulador cableado). 94 tests pasan, 4 skips por dependencia. El revisor encontró 4 fallas; corregidas (incl. seed sin floats).
- 2026-10-17 03:57 · Spec 05-policies ☑: división (prorrateada y literal), términos, match, recálculo y scoring v1 como funciones puras.
- 2026-10-17 03:57 · Spec 06-recaudo ☑: GET/POST /me/collection-account y ensure_collection_account (commit antes de Go, reintento tras revocación o pool agotado). Se activó 03 AC-6. 127 tests pasan, 3 skips (03 AC-8 → 07, 03 AC-11 → 09/11, rutas completas → final).
- 2026-10-17 04:30 · Spec 07-sales ☑: webhook de Puntored (HMAC sobre el cuerpo crudo, dedup, división en 3 patas, parte Trust con mínimo y barrido al pagar), ventas de hoy con cursor, historial y detalle. Catálogo de notificaciones y notify() creados (14 completa endpoints). Se activó 03 AC-8. 150 tests, 2 skips (03 AC-11, rutas completas). Revisor: 7 fallas corregidas (cursor que perdía ventas, created_at tras el lock, payloads con NaN/\u0000/montos fuera de bigint).
- 2026-10-17 05:05 · Spec 08-worker ◐ (código completo; AC-9/AC-10 se activan con 11): run_once con J1–J6, envío y consulta de transferencias con reintentos y backoff, reconciliación del simulador, cierre diario. 182 tests, 4 skips. Revisor: 5 fallas corregidas (404 de Go dejaba la pata en processing para siempre, reintento sobre cuenta revocada).
- 2026-10-17 05:38 · Spec 09-scoring ☑: features por mes/semana/día local, perfil con caché diaria, cupo usado/disponible, vista del prestamista con franja de ventas. El seed deja a Tienda La Esquina en grado A con cupo $6.000.000.
- 2026-10-17 05:38 · Spec 10-marketplace ☑: solicitudes, ofertas (count sobre todas las páginas, solicitud virtual), matches y cancelaciones. 209 tests, 4 skips (03 AC-11 y 08 AC-9/10 → 11; rutas completas → final). Revisor: 4 fallas corregidas (montos y offset fuera de bigint daban 500).
- 2026-10-17 06:19 · Spec 03-platform-dev ☑: AC-11 activo (cierre de periodo desde /dev y seed de créditos El Trigal 58 %, Nova 12 %, Marta 32 % con snapshot de términos en Money).
- 2026-10-17 06:19 · Spec 08-worker ☑: AC-9 y AC-10 activos con las funciones de la spec 11.
- 2026-10-17 06:19 · Spec 11-loans ☑: preview, crear (3 orígenes), aceptar/rechazar/cancelar con reapertura, desembolso con link de autorización y clave por intento, on_disbursement_update, expire_pending, close_period, listas y detalle. 232 tests, 1 skip (rutas completas, fase final). Revisor: 2 fallas corregidas (aceptar un crédito vencido lo revivía).
- 2026-10-17 06:47 · Spec 12-repayments ☑: lista de abonos por rol con filtros locales y entrega, y traza venta → abono → pagos → crédito.
- 2026-10-17 06:47 · Spec 13-dashboard ☑: dashboards de comercio y prestamista; con el seed dan cupo $4.000.000 de $6.000.000, 32 % pagado, 3 ofertas para ti, Camilo con 3 créditos y $5.500.000 prestados. 250+ tests, 1 skip (final). Revisor: fecha 9999-12-31 daba 500 en filtros locales, corregido.
- 2026-10-17 07:16 · Spec 14-notifications ☑: lista con unread_count, leer todas y una; todos los emisores usan el catálogo y su data cumple NotificationData.
- 2026-10-17 07:16 · Spec 15-waitlist ☑: JSON y urlencoded (sin python-multipart), upsert sin 409, honeypot, 303 para formularios, rate limit por IP y CORS.
- 2026-10-17 07:16 · Spec 16-admin ☑: dashboard de ingresos y fondos retenidos por asset, reintento de patas y revocación de cuentas (commit local antes de Go). Se activó 00-core AC-6 completo: todas las rutas del YAML implementadas, sin extras. Suite completa sin skips.
- 2026-10-17 07:53 · Fase final (sin commit todavía): smoke E2E verde con el fake (29 pasos) y con `OP_CLIENT=http` contra el mock (30 pasos, `scripts/smoke_http.sh`), mock HTTP de op-service con sus tests, Dockerfile + servicio `api` en compose (verificado con `docker compose up`: healthy y `/api/v1/health` ok), `.env.example` completo y `backend/README.md`. 298 tests, ruff limpio, sin bugs nuevos en la app.
- 2026-10-17 08:26 · Integración: `greenfield/backend` pusheada y mergeada a main con `--no-ff` (`e70cb2d`, sin conflictos, árbol idéntico al de la rama). Sobre main: 298 tests, ruff limpio, smoke fake 29 pasos, smoke http 30 pasos; push de main. Por pedido del usuario se borró `greenfield/backend` (local y origin) y la revisión final sigue en `bugfix/backend`.
- 2026-10-17 08:56 · Revisión final adversarial (contrato, dinero/concurrencia, seguridad): 17 hallazgos (15 distintos), 14 corregidos con test de regresión y 1 sin cambiar (rate limit de login, ver Decisiones). Sin cambios de contrato. 318 tests, ruff limpio, smoke fake 29 pasos, smoke http 30 pasos, imagen Docker healthy. Para la demo: el admin requiere `DEMO_ADMIN_PASSWORD` y Postgres de compose escucha solo en loopback.
