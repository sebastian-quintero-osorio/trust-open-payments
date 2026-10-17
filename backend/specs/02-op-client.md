# 02 · Cliente de op-service (Go)

Estado: ☐ pendiente · Fase: 0 · Dueño: — · Carpeta: `app/integrations/op/`

## Objetivo
Un único punto de contacto con op-service: `OpClient` (Protocol) con una implementación HTTP real y una falsa en memoria que permite construir y probar todo Trust sin Go. Contrato: `contracts/op-service.openapi.yaml`.

## Endpoints
Ninguno (los endpoints de demo que manejan el fake están en `03-platform-dev`).

## Reglas
- **R1. Protocol** (`client.py`), síncrono (SQLAlchemy sync + hilo del worker). Un método por endpoint del OpenAPI, con los nombres de `00-core`:
  ```python
  class OpClient(Protocol):
      def resolve_wallet_address(self, url: str) -> WalletAddress: ...                       # GET    /v1/wallet-addresses?url=
      def create_collection_account(self, body: CreateCollectionAccountRequest, idempotency_key: str) -> CollectionAccount: ...  # POST /v1/collection-accounts
      def get_collection_account(self, collection_account_id: str) -> CollectionAccount: ...  # GET    /v1/collection-accounts/{id}
      def find_collection_accounts(self, external_ref: str) -> list[CollectionAccount]: ...   # GET    /v1/collection-accounts?external_ref=  (items)
      def revoke_collection_account(self, collection_account_id: str) -> None: ...           # DELETE /v1/collection-accounts/{id} (204, sin Idempotency-Key)
      def create_transfer(self, body: CreateTransferRequest, idempotency_key: str) -> Transfer: ...         # POST /v1/transfers
      def get_transfer(self, transfer_id: str) -> Transfer: ...                              # GET    /v1/transfers/{id}
      def create_payment(self, body: CreatePaymentRequest, idempotency_key: str) -> Payment: ...            # POST /v1/payments
      def get_payment(self, payment_id: str) -> Payment: ...                                 # GET    /v1/payments/{id}
      def create_sim_qr_payment(self, body: CreateSimQrPaymentRequest, idempotency_key: str) -> SimQrPayment: ...  # POST /v1/sim/puntored/qr-payments
      def list_sim_qr_payments(self, after: int = 0, limit: int = 100) -> SimQrPaymentList: ...  # GET /v1/sim/puntored/qr-payments
      def health(self) -> Health: ...                                                        # GET    /v1/health
  ```
- **R2. Schemas** (`schemas.py`, Pydantic v2): espejo 1:1 de `components/schemas` del OpenAPI: `Amount`, `PositiveAmount` (value > 0), `WalletAddress`, `CreateCollectionAccountRequest`, `CollectionAccount`, `CreateTransferRequest`, `Transfer`, `CreatePaymentRequest`, `Payment`, `CreateSimQrPaymentRequest`, `SimQrPayment`, `SimQrPaymentList`, `PuntoredPaymentEvent`, `Health`. Estados y `failure_code` como `Literal`. Requests con `extra="forbid"`, responses con `extra="ignore"`. `Amount.from_money(m)` / `.to_money()` convierten con `core/money.py`.
- **R3. Errores** (`client.py`): `OpError(status, code, message, retryable)` para 4xx y `OpUnavailable(message)` para 5xx, timeout o error de conexión. Los llamadores tratan `OpError.retryable == True` (p. ej. `409 request_in_progress`) igual que `OpUnavailable`: reintentan con la **misma** clave.
- **R4. HttpOpClient** (`http.py`): `httpx.Client(base_url=OP_SERVICE_BASE_URL, timeout=OP_HTTP_TIMEOUT_SECONDS)`, header `X-Internal-Api-Key: OP_SERVICE_API_KEY` en todo, `Idempotency-Key` en todo POST. Cuerpo = `model_dump(mode="json", exclude_none=True)` (mismo modelo → mismo JSON). Acepta `transport` opcional (tests con `httpx.MockTransport`).
- **R5. Mapeo HTTP**: 2xx → schema (204 → `None`; `Idempotent-Replayed: true` se trata igual). 4xx con cuerpo `{"error":{code,message,retryable}}` → `OpError` con esos campos; 4xx sin cuerpo válido → `OpError(status, "http_<status>", texto[:200], False)`. 5xx, `httpx.TimeoutException`, `httpx.TransportError` o 2xx que no valida contra el schema → `OpUnavailable`.
- **R6.** El cliente **nunca reintenta** por su cuenta: exactamente una petición por llamada.
- **R7.** `health()` usa timeout de 2 s (para que `GET /health` no se cuelgue 10 s con Go caído).
- **R8.** Log de cada llamada: método, ruta, status, ms, Idempotency-Key. Nunca la API key.
- **R9. FakeOpClient** (`fake.py`): todo en memoria, protegido con `threading.Lock`. Valida como Go: cuenta inexistente → `OpError(404,"not_found")`; cuenta no activa → `409 collection_account_not_active`; asset distinto al de la cuenta → `422 asset_mismatch`.
- **R10. Idempotencia del fake**: por (método, clave) guarda el JSON canónico del cuerpo y el resultado (respuesta o `OpError` 4xx). Misma clave + mismo cuerpo → mismo resultado (mismo id); otro cuerpo → `OpError(409,"idempotency_conflict",…, False)`. Lo inyectado con `fail_next` no se guarda.
- **R11. Ids**: `uuid5(NS, f"{salt}:{tipo}:{n}")` con contador por tipo. `salt="test"` en tests (determinista); `get_op_client()` usa un salt aleatorio por proceso para no chocar con ids ya guardados en Postgres.
- **R12. Registro y controles del fake**: `calls: list[FakeCall(method, args, idempotency_key)]`, `calls_to(method)`, `reset()`, `fail_next(method, exc)` (la próxima llamada a ese método lanza `exc`: `OpError` u `OpUnavailable`).
- **R13. Wallets en el fake**: cualquier URL `https://…` o `$host/path` resuelve (normaliza `$`→`https://`, quita `/` final) con el asset de mercado y `public_name` = último segmento. `set_wallet(url, asset_code, asset_scale, public_name=None, exists=True)` lo sobreescribe; `exists=False` → `422 wallet_not_found`. Otro esquema → `422 invalid_url`.
- **R14. Cuentas de recaudo en el fake**: pool en memoria (`pool_size=5`, `https://ilp.interledger-test.dev/trust-recaudo-NN`, asset de mercado). `create` asigna la siguiente libre; `external_ref` con una `active` → `409 already_assigned`; sin libres → `409 pool_exhausted`. `qr_payload = https://pay.trustservices.com.co/qr/{id}`. `revoke` → `revoked` (la wallet no vuelve al pool). `find` incluye revocadas, la más reciente primero. `adopt_collection_account(op_id, external_ref, wallet_address, qr_payload, asset_code, asset_scale, status)` registra una cuenta ya existente.
- **R15. Reinicio en modo fake**: al arrancar con `OP_CLIENT=fake`, el lifespan carga todas las filas de `collection_accounts` en el fake con `adopt_collection_account`. Así un reinicio no rompe `/dev/sales/simulate`. Las transferencias y pagos en vuelo sí se pierden (aceptable en dev).
- **R16. Transferencias en el fake**: `create` → `processing` con URLs `null`. `complete_transfer(id, receive=None)` → `completed`, `sent_amount = debit_amount`, `receive_amount = receive or debit_amount`, URLs y `completed_at`. `fail_transfer(id, code, retryable, sent=0)` → `failed`, `failure_code`, `retryable`, `sent_amount = sent`; con `grant_revoked` también revoca la cuenta.
- **R17. Pagos en el fake**: `create` → `awaiting_authorization`, `debit_amount = receive_amount`, `authorization_url = {PUBLIC_API_URL}/api/v1/dev/fake-wallet/authorize/{payment_id}`, `authorization_expires_at = now + 10 min`; guarda el request (`get_payment_request(id).return_url`). `approve_payment(id)` → `processing` (`authorization_url = null`); `complete_payment(id)` → `completed` (`sent_amount = debit_amount`); `reject_payment(id)` → `rejected`; `expire_payment(id)` → `expired`. Los terminales nunca cambian (transición inválida → `ValueError`).
- **R18. Auto-completar** (`OP_FAKE_AUTO_COMPLETE`, true en dev, false en tests): el primer `get_transfer` de una `processing` la completa; el primer `get_payment` de una `processing` la completa. Un pago nunca pasa solo de `awaiting_authorization`: eso lo hace `/dev/fake-wallet/authorize`.
- **R19. Simulador en el fake**: `create_sim_qr_payment` completa al instante: `seq` creciente, `bre_b_reference = BREB-SIM-{seq:06d}`, `status = completed` y `webhook_event` = `PuntoredPaymentEvent` (`type = qr_payment.received`, `occurred_at = clock.now()`, datos de la cuenta). No envía HTTP. `list_sim_qr_payments(after, limit)` devuelve `seq > after` ascendente y `next_after`.
- **R20.** `health()` del fake → `{status: "ok", client_wallet_address: "https://ilp.interledger-test.dev/trust"}`.
- **R21. `get_op_client()`**: singleton por proceso según `OP_CLIENT` (`fake` | `http`); dependencia FastAPI y del worker. El `fake_op` de `conftest` es esa misma instancia (con `salt="test"`, sin auto-completar) y se hace `reset()` entre tests.

## Datos
- No toca la base, salvo la carga de R15 (lee `collection_accounts`).

## Depende de
- `00-core` (config, `core/money.py`, `core/clock.py`).

## Criterios de aceptación
- [ ] **AC-1.** Dado el fake, cuando llamo `create_transfer` dos veces con la misma clave y el mismo cuerpo, entonces recibo el mismo `id` y solo existe una transferencia (→ `test_ac1_fake_idempotent_replay`).
- [ ] **AC-2.** Dada una clave ya usada, cuando la reuso con otro `debit_amount`, entonces `OpError` 409 `idempotency_conflict` (→ `test_ac2_fake_idempotency_conflict`).
- [ ] **AC-3.** Dado `HttpOpClient` con `MockTransport`, cuando llamo `create_transfer(body, "payout:X:1")`, entonces la petición lleva `X-Internal-Api-Key`, `Idempotency-Key: payout:X:1` y el JSON sin campos `null` (→ `test_ac3_http_headers_and_body`).
- [ ] **AC-4.** Dada una respuesta 422 `{"error":{"code":"wallet_not_found","message":"…","retryable":false}}`, entonces `OpError(422, "wallet_not_found", …, False)`; y un 409 `request_in_progress` sale con `retryable=True` (→ `test_ac4_http_maps_4xx`).
- [ ] **AC-5.** Dado un 502, un timeout o un error de conexión, entonces `OpUnavailable` y el transport recibió exactamente 1 petición (→ `test_ac5_http_5xx_no_retry`).
- [ ] **AC-6.** Dado el fake con `pool_size=1`, cuando asigno `merchant:A`, después otra vez `merchant:A` con otra clave y después `merchant:B`, entonces 201, `409 already_assigned` y `409 pool_exhausted` (→ `test_ac6_fake_collection_pool`).
- [ ] **AC-7.** Dada una transferencia de 720, cuando `fail_transfer(id, "insufficient_balance", True, sent=200)`, entonces `get_transfer` da `failed`, `sent_amount.value == "200"`, `retryable` true; y `complete_transfer` de otra deja `sent_amount == debit_amount` (→ `test_ac7_fake_transfer_lifecycle`).
- [ ] **AC-8.** Dado un pago creado, `approve_payment` → `processing`, `complete_payment` → `completed`; y `reject_payment` sobre un `completed` lanza `ValueError` (→ `test_ac8_fake_payment_lifecycle`).
- [ ] **AC-9.** Dada una cuenta activa, `create_sim_qr_payment` de 4800 devuelve `completed` con `webhook_event.data.amount.value == "4800"`, y `list_sim_qr_payments(after=0)` lo incluye con `next_after == seq` (→ `test_ac9_fake_sim_qr_payment`).
- [ ] **AC-10.** `resolve_wallet_address("$ilp.interledger-test.dev/tienda/")` → `url == "https://ilp.interledger-test.dev/tienda"`; con `set_wallet(url, exists=False)` → `422 wallet_not_found`; `fail_next("health", OpUnavailable("x"))` hace fallar solo la siguiente llamada (→ `test_ac10_fake_wallets_and_fail_next`).
- [ ] **AC-11.** `get_op_client()` devuelve `FakeOpClient` con `OP_CLIENT=fake` y `HttpOpClient` con `OP_CLIENT=http`, siempre la misma instancia (→ `test_ac11_get_op_client`).
- [ ] **AC-12.** Dada una fila en `collection_accounts` y un fake recién creado, tras el arranque `create_sim_qr_payment` sobre esa cuenta funciona (→ `test_ac12_fake_adopts_accounts_on_boot`).

## Fuera de alcance
- `/v1/admin/pool-wallets*`, `/v1/interact/finish/*` y `/qr/*` (no los llama Trust).
- Reintentos, backoff y circuit breaker (son de cada llamador, spec 08).
