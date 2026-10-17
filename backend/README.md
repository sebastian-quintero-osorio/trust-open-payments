# Backend Trust

API de Trust (FastAPI + Postgres): créditos para MiPymes que se pagan solos con sus ventas. Cada pago Bre-B que recibe
el QR de un negocio llega a una cuenta de recaudo de Trust; la API divide la venta (abono al prestamista, parte de Trust
y el resto al negocio) y pide las transferencias por Open Payments. Además calcula el cupo de cada negocio, cruza
solicitudes con ofertas y lleva el ciclo del crédito: preview, aceptación, desembolso autorizado en la wallet del
prestamista, abonos y recálculo del porcentaje.

- **Contrato HTTP con web y móvil** (fuente de verdad de rutas, campos y códigos):
  [`contracts/trust-api.openapi.yaml`](../contracts/trust-api.openapi.yaml)
- **Contrato con op-service (Go)**: [`contracts/op-service.openapi.yaml`](../contracts/op-service.openapi.yaml)
- **Specs por módulo** (comportamiento y criterios de aceptación): [`specs/`](specs/README.md), empezando por
  [`00-core`](specs/00-core.md) y [`01-data-model`](specs/01-data-model.md)

## Arquitectura

```
 web / app móvil ──HTTPS──▶ Trust API (este repo) ──HTTP + X-Internal-Api-Key──▶ op-service (Go)
                             FastAPI, 1 proceso      ◀── webhook firmado ──────── │  Open Payments (wallets,
                             + hilo worker                (pago Bre-B recibido)   │  grants, transferencias)
                                   │                                             │  + simulador de Puntored
                                Postgres 16                                       ▼
                                                                         wallets Interledger
```

- **Trust API** (`app/`): toda la lógica de negocio. Un solo proceso (`uvicorn app.main:app --workers 1`): el worker
  corre como hilo daemon dentro de la API y hace por polling todo lo que depende de Go: enviar y seguir las
  transferencias de cada venta, seguir desembolsos, vencer créditos pendientes, reconciliar webhooks perdidos (cada
  30 s) y el cierre diario del porcentaje.
- **op-service** (Go, otro equipo): la única pieza que habla Open Payments. Trust lo usa a través de un `OpClient` con
  dos implementaciones (spec 02):
  - `OP_CLIENT=fake`: cliente en memoria para desarrollar y hacer la demo sin Go. El desembolso se autoriza en una
    wallet falsa servida por la misma API (`/api/v1/dev/fake-wallet/authorize/{id}`).
  - `OP_CLIENT=http`: `HttpOpClient` contra op-service real o contra el mock de
    [`tools/mock_op_service/`](tools/mock_op_service/README.md), que implementa el mismo contrato en memoria.
- **Postgres**: 15 tablas creadas al arrancar (`create_all`); el dinero va en enteros (unidades mínimas) y los
  porcentajes en bps.

| Carpeta | Qué hay |
|---|---|
| `app/core` | Configuración, base de datos, errores, dinero, reloj, seguridad (JWT, bcrypt) |
| `app/integrations/op` | `OpClient`, `HttpOpClient`, `FakeOpClient` y los schemas espejo del contrato de Go |
| `app/policies` | Funciones puras: división de la venta, términos, match, recálculo del %, scoring |
| `app/modules/*` | Un módulo por spec: users, recaudo, sales, scoring, marketplace, loans, repayments, dashboard, notifications, waitlist, admin, platform (health, config, `/dev`) |
| `app/worker.py` | Jobs J1–J6 del worker (spec 08) |
| `tests/` | Un test por criterio de aceptación, contra Postgres real y validando contra el YAML |
| `scripts/` | Smoke E2E (`smoke_e2e.py`, `smoke_http.sh`) |
| `tools/mock_op_service/` | Mock HTTP de op-service |

## Arranque local

Requisitos: Docker (o OrbStack), [uv](https://docs.astral.sh/uv/) y Python 3.12 (uv lo instala si falta).

```bash
cd backend
docker compose up -d db          # Postgres 16 en localhost:5433 (trust/trust), bases trust_dev y trust_test
uv sync                          # dependencias, incluido el grupo dev
cp .env.example .env             # y pon JWT_SECRET (openssl rand -hex 32); DEV_ENDPOINTS_ENABLED=true para la demo
uv run uvicorn app.main:app --workers 1 --port 8000
```

- La API queda en `http://localhost:8000/api/v1` y la documentación interactiva en `http://localhost:8000/docs`.
- `GET /api/v1/health` debe responder `{"status": "ok", "db": "ok", "op": "ok", ...}`.
- Siempre `--workers 1`, porque el worker vive dentro del proceso. Para desarrollar sirve `--reload`.
- Si la base `trust_test` no existe (volumen de Postgres nuevo), créala una vez:
  `docker compose exec db psql -U trust -d trust_dev -c "CREATE DATABASE trust_test"`.

## Tests

```bash
uv run pytest -q          # toda la suite, contra Postgres (trust_test)
uv run ruff check .       # lint
```

- Los tests usan `TEST_DATABASE_URL` (por defecto `postgresql+psycopg://trust:trust@localhost:5433/trust_test`),
  recrean las tablas por sesión y las truncan entre tests. No leen `backend/.env`.
- `assert_contract(response, operation_id)` valida status y body de cada respuesta contra `trust-api.openapi.yaml`.
  Un test compara además el conjunto de rutas de la app con el del YAML.
- `tests/test_mock_op_service.py` valida el mock contra `op-service.openapi.yaml` y corre el `HttpOpClient` real
  contra él.

## Seed y demo

Con `DEV_ENDPOINTS_ENABLED=true` existen los atajos `/api/v1/dev/*` (con false responden 404):

| Endpoint | Para qué |
|---|---|
| `POST /dev/seed` | Crea (idempotente) 3 comercios, 1 prestamista y 1 admin, sus wallets y cuentas de recaudo, historial de ventas, 4 ofertas abiertas y 2 créditos activos |
| `POST /dev/sales/simulate` | Simula un pago Bre-B al QR de un negocio (por op-service: con el fake se procesa en el acto; con `http` llega por webhook) |
| `POST /dev/loans/{id}/close-period` | Fuerza la revisión del porcentaje del periodo |
| `GET /dev/fake-wallet/authorize/{payment_id}` | Wallet falsa del prestamista (solo `OP_CLIENT=fake`): Aprobar / Rechazar el desembolso |

Usuarios del seed (contraseña `DEMO_PASSWORD`, por defecto `trust2026`): `marta@demo.trust` (Tienda La Esquina, grado A,
cupo $6.000.000), `trigal@demo.trust`, `nova@demo.trust`, `camilo@demo.trust` (prestamista, rango $1.000.000–$2.500.000)
y `admin@demo.trust`.

Flujo de la demo (lo que recorre el smoke, paso a paso):

```bash
API=http://localhost:8000/api/v1
curl -s -X POST $API/dev/seed -H 'Content-Type: application/json' -d '{}'
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"marta@demo.trust","password":"trust2026"}' | jq -r .access_token)
curl -s $API/me/credit-profile -H "Authorization: Bearer $TOKEN"          # cupo $6.000.000
MARTA=$(curl -s $API/me -H "Authorization: Bearer $TOKEN" | jq -r .id)
curl -s -X POST $API/dev/sales/simulate -H 'Content-Type: application/json' \
  -d "{\"merchant_id\":\"$MARTA\",\"amount\":{\"value\":\"4800000\",\"asset_code\":\"COP\",\"asset_scale\":2}}"
curl -s $API/sales/today -H "Authorization: Bearer $TOKEN"                # $48.000, 100 % al negocio
```

Después: Marta publica una solicitud (`POST /loan-requests`), ve las ofertas que le encajan
(`GET /loan-offers?scope=market`), toma una (`POST /loans`); Camilo desembolsa (`POST /loans/{id}/disburse` con
`{"client": "web"}`), abre el `authorization_url` y aprueba; el worker deja el crédito `active` y desde ahí cada venta
simulada reparte 85/15 y muestra el abono en `/sales/today`, `/repayments` y los dashboards.

## Smoke E2E

Arranca la API real en un subproceso (base `trust_smoke` desechable, puerto libre, worker cada 1 s), recorre la demo
completa por HTTP y valida cada respuesta contra el YAML y las cifras de las specs. No depende de `backend/.env`.

```bash
docker compose up -d db
uv run python scripts/smoke_e2e.py      # OP_CLIENT=fake: 29 pasos
./scripts/smoke_http.sh                 # OP_CLIENT=http contra el mock de op-service: 30 pasos
```

- `smoke_http.sh` es `smoke_e2e.py --with-mock`: arranca el mock en otro puerto libre con API key y secreto de webhook
  aleatorios, aprueba el desembolso en la página de consentimiento del mock y al final comprueba en el mock que los 5
  webhooks firmados se entregaron (Trust respondió 2xx), es decir, que las ventas no llegaron solo por la reconciliación.
- Opciones: `--keep-db`, `--timeout S` (por polling, 60 s), `--db-name`, `--pg-url`, `--port`; contra otro op-service:
  `--op-client http --op-url URL --op-api-key K --webhook-secret S`. Detalle en el docstring de `scripts/smoke_e2e.py`.
- Salida: 0 todo bien, 1 falló un paso (imprime el final de los logs de la API y del mock), 2 no se pudo preparar el
  entorno.

### Trust contra el mock, a mano

```bash
# terminal 1: mock de op-service
TRUST_WEBHOOK_URL=http://localhost:8000/api/v1/webhooks/puntored/payments TRUST_WEBHOOK_SECRET=dev-webhook-secret \
  uv run uvicorn tools.mock_op_service.app:app --port 8090
# terminal 2: la API, con esto en .env
#   OP_CLIENT=http
#   OP_SERVICE_BASE_URL=http://localhost:8090
#   OP_SERVICE_API_KEY=dev-internal-api-key
#   PUNTORED_WEBHOOK_SECRETS=dev-webhook-secret
uv run uvicorn app.main:app --workers 1 --port 8000
```

El mock guarda todo en memoria: si lo reinicias, recrea `trust_dev` (o ver `MOCK_SIM_SEQ_START` en su
[README](tools/mock_op_service/README.md)).

## Deploy con docker compose (EC2)

`Dockerfile`: `python:3.12-slim` + uv, solo dependencias de producción, usuario sin privilegios (uid 10001),
`uvicorn --workers 1` en el puerto 8000 y `HEALTHCHECK` sobre `/api/v1/health` (503 con la base caída → unhealthy).
`docker-compose.yml` tiene `db` (Postgres 16) y `api` (esta imagen, espera a que `db` esté healthy, lee `.env`).

```bash
# en la instancia (Docker y el plugin compose instalados), con el repo clonado
cd backend
cp .env.example .env
# editar .env: JWT_SECRET, PUNTORED_WEBHOOK_SECRETS, TRUST_REVENUE_WALLET_ADDRESS, OP_CLIENT=http,
# OP_SERVICE_BASE_URL, OP_SERVICE_API_KEY, PUBLIC_API_URL, CORS_ORIGINS; DEV_ENDPOINTS_ENABLED solo para demo
docker compose up -d --build
curl -s http://localhost:8000/api/v1/health
docker compose logs -f api
```

- Dentro de compose la API usa `db:5432` aunque `.env` diga `localhost:5433`. Para otra base (RDS) define
  `TRUST_API_DATABASE_URL`; para otro puerto del host, `TRUST_API_PORT` (en el shell o en `.env`).
- `docker compose up -d db` funciona sin `.env`; la API no arranca sin `JWT_SECRET`.
- HTTPS: descomenta el servicio `caddy` de `docker-compose.yml` (certificados automáticos de Let's Encrypt), apunta el
  DNS del dominio a la instancia, abre 80/443 en el security group y deja de publicar el 8000. El rate limit de la
  lista de espera toma la IP de `X-Forwarded-For`, que pone Caddy.
- Security group: no abras 5433 (Postgres usa `trust`/`trust`) ni 8000 si usas Caddy.
- Si op-service corre en la misma máquina fuera de compose, la API lo alcanza en `http://host.docker.internal:<puerto>`.
- Actualizar: `git pull && docker compose up -d --build api`. Las tablas nuevas se crean al arrancar, pero
  `create_all` no altera tablas existentes: un cambio de columnas pide recrear la base (no hay migraciones en el MVP).

## Configuración

Todas las variables, con sus defaults y su uso, están en la tabla "Configuración" de
[`specs/00-core.md`](specs/00-core.md#configuración-coreconfigpy-variables-de-entorno) y en
[`.env.example`](.env.example) (valores seguros para desarrollo, sin secretos). Obligatorias: `DATABASE_URL` y
`JWT_SECRET` (≥ 32 caracteres). Con `OP_CLIENT=http` también `OP_SERVICE_BASE_URL`, `OP_SERVICE_API_KEY`,
`PUNTORED_WEBHOOK_SECRETS` y `TRUST_REVENUE_WALLET_ADDRESS`.

## Notas para el equipo de Go (op-service)

- **El contrato manda**: [`contracts/op-service.openapi.yaml`](../contracts/op-service.openapi.yaml). Trust no lo
  modifica; el `HttpOpClient` y el mock salen de ahí. Si algo tiene que cambiar, se cambia el YAML primero y se avisa.
- **Auth**: todas las llamadas de Trust llevan `X-Internal-Api-Key` (`OP_SERVICE_API_KEY` en Trust = tu
  `INTERNAL_API_KEY`).
- **Idempotency-Key** en cada POST, con claves estables para que un reintento nunca duplique plata:

  | Operación | Clave |
  |---|---|
  | `POST /v1/collection-accounts` | `merchant:{merchant_id}:recaudo:{n}` (n sube tras una revocación o un `409 pool_exhausted`) |
  | `POST /v1/transfers` | `payout:{payout_id}:{attempt}` |
  | `POST /v1/payments` (desembolso) | `loan:{loan_id}:disb:{attempt}` |
  | `POST /v1/sim/puntored/qr-payments` | `sim:{uuid}` |

  En cuentas, transferencias y desembolsos, Trust reintenta con la misma clave ante 5xx, timeouts, 401 y errores con
  `retryable: true`; solo cambia de clave (sube `n` o `attempt`) cuando el intento anterior terminó: un 4xx
  definitivo, un desembolso rechazado, vencido o fallido, o una transferencia fallida que se vuelve a intentar. Por
  eso Go debe repetir la respuesta guardada (2xx y 4xx definitivas) y responder `409 idempotency_conflict` si llega la
  misma clave con otro cuerpo.
- **Webhook `puntoredPayment`** → `POST {Trust}/api/v1/webhooks/puntored/payments` con
  `X-Puntored-Signature: t=<unix>,v1=<hex(HMAC-SHA256(secret, t + "." + body_crudo))>` (hex en minúsculas). Trust
  verifica en tiempo constante sobre el body crudo contra cualquiera de `PUNTORED_WEBHOOK_SECRETS` (rotación) y rechaza
  con 401 si la firma no cuadra (`invalid_signature`) o si `|ahora − t| > 300 s` (`signature_expired`). Responde 200 al
  guardar, también si es duplicado (dedup por `event_id` y por `(collection_account_id, bre_b_reference)`) o si no
  conoce la cuenta. Cada reintento manda los mismos bytes con un `t` nuevo.
- **Reconciliación**: cada 30 s el worker lee `GET /v1/sim/puntored/qr-payments?after=<cursor>` y procesa
  `webhook_event` de cada pago terminal: un webhook perdido no pierde la venta, pero llega hasta 30 s tarde.
- **Desembolso**: `return_url` llega con `loan_id` y `client` en la query; la wallet debe redirigir conservándolos y
  agregar `op_resource=payment`, `op_id` y `op_status`.
- **Health**: Trust llama `GET /v1/health` (timeout 2 s) y resuelve `TRUST_REVENUE_WALLET_ADDRESS` para verificar que esté
  en el asset de mercado (COP, escala 2). Si no, `/api/v1/health` sale `degraded` con un warning.
- Para probar Go contra Trust de punta a punta: `uv run python scripts/smoke_e2e.py --op-client http --op-url
  http://<go>:<puerto> --op-api-key <key> --webhook-secret <secreto>` (Go debe enviar el webhook al puerto que imprime
  el script, que se fija con `--port`). La aprobación usa `authorization_url?decision=approve` como el mock; con una
  wallet real ese paso es manual.

## Notas para web y móvil

- **El contrato manda**: [`contracts/trust-api.openapi.yaml`](../contracts/trust-api.openapi.yaml). La suite de
  backend lo valida en cada respuesta, y el smoke recorre la demo entera contra él.
- Base `/api/v1`, JWT Bearer de `POST /auth/login` (7 días). Dinero siempre como `Money` con `value` string en
  unidades mínimas; porcentajes en bps; fechas UTC con `Z`; "hoy" en America/Bogota.
- Errores: `{"error": {"code", "message", "details"}}`. La UI decide por `code`; `message` ya viene en español.
- Desembolso: abre el `authorization_url` en primer plano. La wallet vuelve a
  `{APP_WEB_URL}/op/return?loan_id=…&client=web|mobile&op_resource=payment&op_id=…&op_status=…`. Toma `op_status`
  solo como pista y confirma con `GET /loans/{id}` cada 2 s hasta `active` o `pending_disbursement`.
- Ventas en vivo: `GET /sales/today?since=<next_since anterior>` cada 2–3 s. Notificaciones: `GET /notifications` cada
  ~30 s.
- Desarrollo local contra esta API: `CORS_ORIGINS` debe incluir el origen del front (por defecto `http://localhost:3000`).
