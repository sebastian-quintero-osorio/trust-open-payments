# Mock HTTP de op-service

Servicio FastAPI **en memoria** que imita a op-service (Go) para correr Trust con `OP_CLIENT=http` sin Go ni Open
Payments. Implementa, según `contracts/op-service.openapi.yaml`, todo lo que llama el `HttpOpClient` de Trust, más
una página de consentimiento simulada para el desembolso y controles `/mock/*` para demos y tests.

## Arranque

Desde `backend/`:

```bash
uv run uvicorn tools.mock_op_service.app:app --port 8090
```

Con webhook hacia un Trust local (puerto 8000):

```bash
TRUST_WEBHOOK_URL=http://localhost:8000/api/v1/webhooks/puntored/payments \
TRUST_WEBHOOK_SECRET=dev-webhook-secret \
uv run uvicorn tools.mock_op_service.app:app --port 8090
```

Y Trust apuntando al mock (en su `.env` o en el entorno):

```bash
OP_CLIENT=http
OP_SERVICE_BASE_URL=http://localhost:8090
OP_SERVICE_API_KEY=dev-internal-api-key       # = INTERNAL_API_KEY del mock
PUNTORED_WEBHOOK_SECRETS=dev-webhook-secret   # = TRUST_WEBHOOK_SECRET del mock
```

Documentación interactiva: `http://localhost:8090/docs`.

uvicorn escucha en `127.0.0.1` por defecto: si Trust corre en Docker, arranca el mock con `--host 0.0.0.0` y desde el
contenedor usa `OP_SERVICE_BASE_URL=http://host.docker.internal:8090` (y `TRUST_WEBHOOK_URL` hacia el puerto publicado
de la API).

## Variables de entorno

Las mismas que op-service cuando existen; las propias del mock empiezan por `MOCK_`. No lee el `.env` de Trust.

| Variable | Default | Uso |
|---|---|---|
| `INTERNAL_API_KEY` | `dev-internal-api-key` | Valor exigido en `X-Internal-Api-Key` (todo `/v1/*` salvo health e interact, y `/mock/*` salvo el consentimiento) |
| `PUBLIC_BASE_URL` | `http://localhost:8090` | Base de `authorization_url` (`/mock/consent/{id}`) y de `qr_payload` (`/qr/{id}`) |
| `POOL_WALLET_ADDRESSES` | `https://ilp.interledger-test.dev/trust-recaudo-01` … `-20` | Pool de wallets de recaudo (coma) |
| `MOCK_ASSET_CODE` / `MOCK_ASSET_SCALE` | `COP` / `2` | Asset del pool y de toda wallet que se resuelva |
| `OP_CLIENT_WALLET_ADDRESS` | `https://ilp.interledger-test.dev/trust` | `client_wallet_address` de `/v1/health` |
| `TRUST_WEBHOOK_URL` | vacío | Destino del webhook `puntoredPayment`. Vacío = no se envía (Trust lo recupera con el listado) |
| `TRUST_WEBHOOK_SECRET` | vacío | Secreto del HMAC de `X-Puntored-Signature` |
| `MOCK_WEBHOOK_RETRY_SECONDS` | `1,5,30,120` | Esperas entre reintentos si Trust no responde 2xx |
| `MOCK_WEBHOOK_TIMEOUT_SECONDS` | `5` | Timeout de cada envío |
| `ALLOWED_RETURN_URL_PREFIXES` | vacío (todo http/https) | Allowlist de `return_url` (coma); fuera de ella → `422 return_url_not_allowed` |
| `SIM_ENABLED` | `true` | `false` → `409 sim_disabled` |
| `SIM_MAX_AMOUNT` | `1000000000` | Tope del simulador en unidades mínimas (10.000.000,00 COP) → `422 invalid_amount` |
| `INTERACTION_TIMEOUT_SECONDS` | `600` | Vigencia del consentimiento (`authorization_expires_at`) |
| `MOCK_SETTLE_SECONDS` | `0` | Tiempo hasta que una transferencia o un pago aprobado queda `completed` (0 = en el siguiente GET) |
| `MOCK_SIM_SEQ_START` | `0` | El primer pago simulado recibe `seq` = este valor + 1 |

## Comportamiento

- **Errores**: siempre `{"error": {"code", "message", "retryable"}}`. Validación del cuerpo o de la query → `400
  validation_error` (nunca el 422 de FastAPI); ids que no son UUID → `404 not_found`.
- **Idempotencia** (todo POST de `/v1/*`): `Idempotency-Key` obligatorio (1–255 caracteres, si no `400`), alcance
  (método, ruta), cuerpo comparado como JSON canónico. Se guardan 72 h las 2xx y las 4xx definitivas (400, 404, 409
  no reintentable, 422) y se repiten con su status original y `Idempotent-Replayed: true`. Nunca se guardan 5xx ni
  401. Misma clave con otro cuerpo → `409 idempotency_conflict`. Cada operación es atómica, así que
  `409 request_in_progress` no ocurre.
- **Wallets** (`GET /v1/wallet-addresses`): acepta `https://…` y `$host/path`, quita el `/` final; otro esquema →
  `422 invalid_url`. Toda wallet existe en `MOCK_ASSET_CODE/SCALE` salvo que `/mock/wallets` diga otra cosa.
- **Cuentas de recaudo**: toman la siguiente wallet libre del pool (`409 pool_exhausted` si no hay o el asset no es el
  del pool); `409 already_assigned` si el `external_ref` ya tiene una `active`. `DELETE` es idempotente (204) y la
  wallet revocada no vuelve al pool. `ledger_balance` = entradas del simulador − transferencias completadas.
- **Transferencias**: el POST valida (cuenta activa → 409, asset → 422, receptor resoluble → 422) y responde `201
  processing` sin URLs de Open Payments. Un GET posterior a `created_at + MOCK_SETTLE_SECONDS` la deja `completed`
  (`sent_amount = receive_amount = debit_amount`, URLs y `completed_at`). Si la cuenta se revocó antes de ejecutarla:
  `failed` con `grant_revoked`.
- **Pagos (desembolso)**: `201 awaiting_authorization` con `debit_amount = receive_amount` (sin comisiones ni tasa de
  cambio) y `authorization_url = {PUBLIC_BASE_URL}/mock/consent/{payment_id}`. Esa página hace de wallet del
  prestamista: sin parámetros muestra Aprobar/Rechazar; `?decision=approve|reject` pasa el pago a `processing` o
  `rejected` y hace 302 al `return_url` agregando `op_resource=payment`, `op_id` y `op_status` a su query (sin borrar
  `loan_id`, `client`, …). El attempt es de un solo uso: repetir solo redirige con el estado actual. Un GET posterior a
  la aprobación + `MOCK_SETTLE_SECONDS` lo deja `completed`. Sin aprobación, pasa a `expired` en
  `authorization_expires_at + 30 s` (y aprobar después de `authorization_expires_at` también lo vence).
  `GET /v1/interact/finish/{attempt_id}` también existe (sin verificar el hash): `interact_ref` + `hash` aprueba,
  `result=grant_rejected|request_denied` rechaza, `invalid_interaction` vence y otro `result` falla con
  `upstream_error`.
- **Simulador de Puntored** (`POST /v1/sim/puntored/qr-payments`): valida (simulador encendido, cuenta existente y
  activa, asset, tope) y el pago queda `completed` en el acto, con `seq`, `bre_b_reference = BREB-SIM-{seq:06d}` y el
  `webhook_event`. Un hilo de fondo envía ese body exacto a `TRUST_WEBHOOK_URL` con
  `X-Puntored-Signature: t=<unix>,v1=<hex(HMAC-SHA256(secret, t + "." + body))>` (`t` nuevo en cada intento, mismos
  bytes y mismo `event_id`) y reintenta según `MOCK_WEBHOOK_RETRY_SECONDS`. El GET lista los pagos terminales con
  `seq > after`, en orden ascendente, hasta `limit` (1–200), con `webhook_delivered_at`.

## Controles `/mock/*` (fuera del contrato; con `X-Internal-Api-Key`)

| Ruta | Cuerpo | Efecto |
|---|---|---|
| `POST /mock/reset` | — | Borra todo el estado (204) |
| `POST /mock/wallets` | `{url, asset_code?, asset_scale?, public_name?, exists?}` | Cómo resuelve una wallet (`exists: false` → `422 wallet_not_found`) |
| `POST /mock/faults` | `{method, path, status, code?, times?}` | Las próximas `times` peticiones a `path` (exacta, o prefijo con `*` final) responden ese status. En un POST se aplica tras mirar la idempotencia, así que un 5xx no se guarda |
| `POST /mock/transfers/{id}/fail` | `{failure_code, retryable?, sent_value?}` | `processing` → `failed` (con `grant_revoked` la cuenta queda `revoked`) |
| `POST /mock/payments/{id}/fail` | `{failure_code?}` | `awaiting_authorization`/`processing` → `failed` |

Ejemplo: simular op-service caído para las transferencias durante 3 peticiones:

```bash
curl -X POST localhost:8090/mock/faults -H 'X-Internal-Api-Key: dev-internal-api-key' \
  -H 'Content-Type: application/json' -d '{"method":"POST","path":"/v1/transfers","status":503,"times":3}'
```

## Límites

- Todo vive en memoria: al reiniciar el mock se pierden las cuentas de recaudo y el `seq` vuelve a empezar. Si Trust
  guardó ids de una corrida anterior, recrea su base de desarrollo (o arranca con `MOCK_SIM_SEQ_START` por encima del
  cursor `sim_qr_payments` de `op_cursors`).
- No implementa `/v1/admin/pool-wallets*` ni las páginas `/qr/{id}` (Trust no las llama); `qr_payload` apunta a una
  ruta que aquí responde 404.
- Sin tasas de cambio ni comisiones: entre assets distintos solo se reescala el valor.

## Tests

`tests/test_mock_op_service.py` (`uv run pytest tests/test_mock_op_service.py`) valida cada respuesta contra los
schemas de `op-service.openapi.yaml`, prueba idempotencia (replay, conflicto, 4xx guardadas, 5xx no guardadas), el
ciclo de transferencias y pagos con el consentimiento, el webhook firmado (verificado con `verify_signature` de Trust)
con sus reintentos, y corre el `HttpOpClient` real de Trust contra el mock.
