# 00 · Core: convenciones que todos respetan

Estado: ☐ pendiente · Fase: 0 · Dueño: — · Carpeta: `app/core/`

## Stack
Python 3.12 · FastAPI · SQLAlchemy 2.0 **sync** · Pydantic v2 + pydantic-settings · httpx · PyJWT · bcrypt · psycopg 3 · pytest. Dependencias con `uv` (`backend/pyproject.toml`). Base de datos **Postgres** también en tests (nada de SQLite: usamos índices parciales, `FOR UPDATE SKIP LOCKED` y JSONB). Tablas con `Base.metadata.create_all()` al arrancar; Alembic queda para después.

## Estructura
```
backend/
  pyproject.toml · Dockerfile · .env.example
  app/
    main.py              # create_app(): routers, CORS, handlers de error, lifespan (crea tablas, con OP_CLIENT=fake carga collection_accounts en el fake (02 R15), arranca el worker)
    worker.py            # loop del worker (spec 08)
    core/                # config.py db.py security.py deps.py money.py clock.py errors.py events.py
    policies/            # funciones puras (spec 05): split.py scoring.py matching.py terms.py repricing.py
    integrations/op/     # cliente de op-service (spec 02): client.py http.py fake.py schemas.py
    modules/<modulo>/    # router.py schemas.py models.py service.py
  tests/                 # conftest.py + test_<modulo>.py
  specs/ · docs/
```
- `router.py` delgado (valida, llama al service, devuelve schema). La lógica y las transacciones viven en `service.py`.
- **Nunca** se llama a op-service dentro de una transacción abierta: primero se hace commit y después se llama (o lo hace el worker).

## API
- **Contrato HTTP**: [`contracts/trust-api.openapi.yaml`](../../contracts/trust-api.openapi.yaml) es la fuente de verdad de rutas, parámetros, códigos de estado y formas de request y response (lo consumen web y móvil). Las specs definen el comportamiento. Si una spec y el YAML difieren en una forma, se corrigen los dos en el mismo cambio, nunca solo el código.
- Base `/api/v1`. JSON en `snake_case`. OpenAPI automático en `/docs`.
- **Auth**: `Authorization: Bearer <JWT>` (HS256, `JWT_SECRET`). Claims: `sub` (user id), `role`, `exp` (`JWT_EXPIRES_HOURS`, 168 por defecto). Dependencias: `current_user` y `require_role("merchant" | "lender" | "admin")`. Web y app móvil usan el mismo token.
- **Roles**: `merchant` (comercio), `lender` (prestamista), `admin` (operador de Trust). Un usuario tiene un solo rol.
- **IDs**: UUID v4 (string).
- **Fechas**: se guardan en UTC (`timestamptz`) y se devuelven en ISO 8601 con `Z`. "Hoy", "por hora" y "periodos" se calculan en `TIMEZONE` (`America/Bogota`) con `core/clock.py` (`now()`, que se puede congelar en tests, `today_local()`, `local_day_bounds(date)` y `format_hora(dt)` → `"10:42 a. m."` en `TIMEZONE`, sin cero inicial, para descripciones congeladas y avisos).
- **Listas**: `?limit=` (50 por defecto, máximo 200) y `?offset=` → `{"items": [...], "next_offset": int | null}`.
- **CORS**: `CORS_ORIGINS` (web y landing). La app móvil no lo necesita.

## Dinero
- En la base: `BIGINT` en unidades mínimas + `asset_code` + `asset_scale` en la misma fila. Nunca float ni Decimal.
- En la API, entrada y salida, siempre el objeto **Money** (igual que Open Payments):
  ```json
  {"value": "4800", "asset_code": "USD", "asset_scale": 2}
  ```
  `value` es un entero en string (48,00 USD). El frontend formatea; el backend no devuelve textos con formato de moneda. **Única excepción**: títulos y cuerpos de notificaciones (spec 14), con `money.format_es(m)` (`$48.000` con escala 0; `$480,00` con escala 2).
- **Escala de los ejemplos**: en todas las specs, los montos son unidades mínimas del asset de mercado (USD, escala 2) = cifras de COPY.md ÷ 10 (venta $48.000 → `4800`, cupo $6.000.000 → `600000`). En prosa se escriben con puntos (`600.000`); en JSON, sin puntos.
- `core/money.py`: `Money` (dataclass inmutable con `value: int`), `to_api()`, `from_api()` (valida patrón `^(0|[1-9][0-9]*)$` y asset), `bps_of(amount, bps) = amount * bps // 10000` (floor), `format_es(m)` (solo para avisos). Prohibido operar montos fuera de estas funciones.
- MVP con **un solo asset de mercado**: `MARKET_ASSET_CODE` / `MARKET_ASSET_SCALE` (USD / 2 en la test wallet). Solicitudes, ofertas, créditos, cuentas de recaudo y wallets propias de negocios deben estar en ese asset (`422 asset_mismatch`). La wallet de un prestamista puede ser de otro asset (Open Payments convierte).
- Porcentajes en puntos básicos (`*_bps`, 15 % = 1500).

## Objetos de referencia (se reutilizan en todas las respuestas)
- `MerchantRef`: `{"id", "business_name", "city", "initials"}` (initials = primeras letras de las dos primeras palabras de business_name, en mayúscula: "Tienda La Esquina" → "TL").
- `LenderRef`: `{"id", "public_name"}` (public_name = `first_name + " " + inicial de last_name + "."`, por ejemplo "Camilo P.").

## Errores
Formato único (también para errores de validación de FastAPI, que se re-mapean):
```json
{"error": {"code": "exceeds_cupo", "message": "El monto supera tu cupo disponible.", "details": {}}}
```
| HTTP | Códigos comunes |
|---|---|
| 400 | `bad_request` |
| 401 | `unauthorized`, `invalid_credentials` |
| 403 | `forbidden` (rol o dueño incorrecto) |
| 404 | `not_found` |
| 409 | conflicto de estado: `invalid_state`, `already_exists`, `active_loan_exists`, `open_request_exists` |
| 422 | `validation_error`, `asset_mismatch`, `exceeds_cupo`, `not_eligible`, `wallet_required`, `no_overlap`, `pct_not_allowed` |
| 429 | `rate_limited` (15) |
| 502/503 | `op_unavailable` (op-service caído o 5xx) |

Cada spec puede agregar códigos propios y debe listarlos en su tabla de endpoints. Códigos propios ya usados (mismo significado en todas las specs): 401 `invalid_signature`, `signature_expired` (07) · 409 `pool_exhausted` (03, 06), `collection_account_not_active` (03, 16), `sim_disabled` (03) · 422 `wallet_not_found`, `invalid_url` (04), `collection_account_required`, `disbursement_rejected` (11).

## Auditoría y notificaciones
- `core/events.py`: `record_event(db, entity_type, entity_id, type, data=None, actor_id=None)` escribe en `events` **dentro de la misma transacción** del cambio de estado. Todo cambio de estado de `loans`, `payouts`, `collection_accounts` y `webhook_events` deja un evento.
- `modules/notifications/service.py`: `notify(db, user_id, type, title, body, data)`, también en la misma transacción. Catálogo de `type` (spec 14): `offer_received`, `loan_accepted`, `action_required`, `loan_active`, `loan_rejected`, `loan_cancelled`, `loan_expired`, `loan_paid`, `loan_late`, `pct_changed`, `abono_received`, `sale_split`, `payout_failed`. Títulos y cuerpos con `modules.notifications.catalog.render(type, audience, **vars)`.

## Configuración (`core/config.py`, variables de entorno)
| Variable | Default | Uso |
|---|---|---|
| `DATABASE_URL` | — | Postgres |
| `JWT_SECRET`, `JWT_EXPIRES_HOURS` | —, 168 | Auth |
| `CORS_ORIGINS` | — | Lista separada por comas |
| `TIMEZONE` | America/Bogota | Día local |
| `MARKET_ASSET_CODE`, `MARKET_ASSET_SCALE` | USD, 2 | Asset único del MVP |
| `OP_CLIENT` | fake | `fake` \| `http` (spec 02) |
| `OP_SERVICE_BASE_URL`, `OP_SERVICE_API_KEY`, `OP_HTTP_TIMEOUT_SECONDS` | —, —, 10 | op-service |
| `OP_FAKE_AUTO_COMPLETE` | true | El fake completa transferencias y pagos en `processing` al primer GET (`conftest` lo pone en false; spec 02) |
| `PUBLIC_API_URL` | http://localhost:8000 | Base del `authorization_url` del fake (spec 02) |
| `PUNTORED_WEBHOOK_SECRETS` | — | Lista separada por comas (spec 07) |
| `NOTIFY_SALE_SPLIT` | true | Aviso `sale_split` por cada venta (spec 07) |
| `TRUST_REVENUE_WALLET_ADDRESS` | — | Receptor de la parte Trust |
| `TRUST_SHARE_BPS`, `TRUST_LEG_MIN` | 2000, 10 | Parte de Trust (spec 05); pata Trust mínima (spec 07) |
| `TRUST_CUT_POLICY` | prorated | `prorated` \| `literal` (spec 05) |
| `MIN_LOAN_AMOUNT`, `AMOUNT_STEP`, `CUPO_CAP` | 5000, 5000, 1000000 | Unidades mínimas del asset de mercado |
| `PCT_OPTIONS_BPS` | 1000,1500,2000 | Porcentajes permitidos |
| `DEFAULT_TERM_MONTHS`, `REPRICE_STEP_BPS`, `REPRICE_MAX_DELTA_BPS` | 3, 250, 500 | Términos y recalculo |
| `MIN_MONTHS`, `SCORING_POLICY` | 3, v1 | Scoring (spec 09) |
| `MIN_SALES_PER_MONTH`, `SCORE_VOLUME_REF` | 10, 1000000 | Mes activo y ventas mensuales que dan volumen máximo (spec 05) |
| `PENDING_LOAN_TTL_HOURS` | 48 | Expiración de créditos pendientes |
| `APP_WEB_URL` | https://app.trustservices.com.co | `return_url` del desembolso |
| `WORKER_ENABLED`, `WORKER_INTERVAL_SECONDS` | true, 2 | Worker (spec 08) |
| `DEV_ENDPOINTS_ENABLED` | false | Habilita `/dev/*` (spec 03) |
| `DEMO_PASSWORD` | trust2026 | Contraseña de los usuarios del seed (spec 03) |
| `DEMO_MERCHANT_WALLET_URL`, `DEMO_LENDER_WALLET_URL` | `https://ilp.interledger-test.dev/demo-<slug>` | Wallets del seed; con `OP_CLIENT=http`, wallets reales en el asset de mercado (spec 03) |
| `LANDING_URL` | https://trustservices.com.co | Destino del 303 del formulario sin JS (spec 15) |

## Contratos entre módulos (nombres fijos)
Las specs de módulo usan exactamente estas funciones. Si una firma cambia, se cambia aquí primero.

| Función | Spec | Uso |
|---|---|---|
| `integrations.op.client.get_op_client() -> OpClient` | 02 | Dependencia FastAPI y worker |
| `OpClient.resolve_wallet_address`, `create_collection_account`, `get_collection_account`, `find_collection_accounts`, `revoke_collection_account`, `create_transfer`, `get_transfer`, `create_payment`, `get_payment`, `create_sim_qr_payment`, `list_sim_qr_payments`, `health` | 02 | Un método por endpoint del OpenAPI. Errores: `OpError(status, code, message, retryable)` para 4xx y `OpUnavailable` para 5xx o timeout |
| `policies.split.split_sale(amount, loan: SplitLoanState \| None, policy="prorated") -> SaleSplit` | 05 | `SplitLoanState(pct_bps, total_repayable, allocated_total, fee_amount, trust_share_bps)` → `SaleSplit(abono, trust_cut, lender_net, merchant_amount, allocated_after, paid_off)` |
| `policies.terms.build_terms(principal, pct_bps, fee_bps, term_months, request_pct_bps \| None, cfg) -> Terms` | 05 | Cargo, total, mínimo mensual, pct min/max |
| `policies.matching.fits(request, offer, cupo_disponible, step) -> Proposal \| None` | 05 | `Proposal(amount, overlap_min, overlap_max, pct_bps)` |
| `policies.repricing.review_period(pct_bps, pct_min_bps, pct_max_bps, monthly_minimum, balance_at_period_start, allocated_in_period, step_bps) -> ReviewDecision` | 05 | `ReviewDecision(new_pct_bps, reason, target_amount, missed)` |
| `policies.scoring.assess(features: MerchantFeatures, cfg) -> Assessment` | 05 | Score, grado, cupo, elegibilidad |
| `modules.scoring.service.get_credit_profile(db, merchant_id) -> CreditProfile` | 09 | Último assessment del día local (o uno nuevo) + cupo usado y disponible |
| `modules.recaudo.service.ensure_collection_account(db, op, merchant) -> CollectionAccount` | 06 | Idempotente; lo usan el endpoint y el seed |
| `modules.sales.service.process_puntored_event(db, event: dict, source) -> WebhookResult` | 07 | Webhook y reconciliación (`source` = `puntored` \| `reconciliation`). `WebhookResult(status, sale_id \| None)`, `status` = `processed` \| `duplicate` \| `unmatched` \| `ignored` |
| `modules.sales.service.today_summary(db, merchant_id, day) -> TodaySummary` | 07 | `TodaySummary(sales_total, sales_count, merchant_total, abonado_total)`; la usan `/sales/today` y 13-dashboard |
| `modules.loans.service.on_disbursement_update(db, loan, payment)` | 11 | El worker la llama con cada GET del pago |
| `modules.loans.service.expire_pending(db, now) -> int` | 11 | Job del worker |
| `modules.loans.service.close_period(db, loan_id, now) -> LoanPctChange` | 11 | Job diario y `/dev/loans/{id}/close-period` |
| `modules.notifications.service.notify(db, user_id, type, title, body, data)` | 14 | Todos |
| `modules.notifications.catalog.render(type, audience, **vars) -> tuple[str, str]` | 14 | Título y cuerpo del catálogo |
| `core.money.format_es(m) -> str` · `core.clock.format_hora(dt) -> str` | 00 | Textos de avisos y descripciones congeladas |
| `core.events.record_event(db, entity_type, entity_id, type, data=None, actor_id=None)` | 00 | Todos |

## Concurrencia
- Bloqueos de fila con `SELECT … FOR UPDATE` en el orden **cuenta de recaudo → crédito → solicitud → oferta → pata (`payouts`)** (siempre el mismo orden, para no crear deadlocks).
- El worker toma filas con `FOR UPDATE SKIP LOCKED`.
- Un solo proceso: `uvicorn app.main:app --workers 1`. El worker corre como hilo daemon iniciado en el lifespan, con su propia sesión.

## Tests
- `pytest` contra Postgres (`TEST_DATABASE_URL`). Las tablas se recrean al iniciar la sesión de tests y se truncan entre tests.
- `conftest.py` provee: `db`, `client` (TestClient con `WORKER_ENABLED=false`, `OP_CLIENT=fake` y `OP_FAKE_AUTO_COMPLETE=false`), `fake_op` (el FakeOpClient de la app, para inspeccionar llamadas; se hace `fake_op.reset()` entre tests), `make_merchant(...)`, `make_lender(...)`, `auth(user)` (headers) y `freeze(dt)` (congela `clock.now()`).
- Los jobs del worker son funciones (`run_once(db, op, now, reconcile=True)`, spec 08) que los tests llaman directo.

## Criterios de aceptación
- [ ] **AC-1.** `GET /api/v1/health` responde 200 con la app arrancada contra Postgres y las tablas creadas (→ `test_ac1_app_boots`).
- [ ] **AC-2.** Un error de validación de FastAPI sale con el formato `{"error": {"code": "validation_error", ...}}` y HTTP 422 (→ `test_ac2_validation_error_format`).
- [ ] **AC-3.** Sin token, un endpoint protegido da 401 `unauthorized`; con rol incorrecto, 403 `forbidden` (→ `test_ac3_auth_roles`).
- [ ] **AC-4.** `Money.from_api` rechaza floats, negativos y strings no numéricos; `bps_of(4800, 1500) == 720` (→ `test_ac4_money`).
- [ ] **AC-5.** `clock.local_day_bounds` usa el día de Bogotá (una venta del 17 oct a las 03:00 UTC cuenta como del 16 oct, porque en Bogotá son las 22:00 del 16) (→ `test_ac5_local_day`).
- [ ] **AC-6.** Test de contrato: el conjunto (método, ruta) de `app.openapi()` es igual al de `contracts/trust-api.openapi.yaml`, y `conftest` expone `assert_contract(response, operation_id)`, que valida status y body contra el schema del YAML (`jsonschema`, resolviendo `$ref`). Cada test de endpoint de las specs lo llama al menos una vez (→ `test_ac6_contract_routes`).
