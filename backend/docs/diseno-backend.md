# Trust: diseño del backend v3 (FastAPI, monolito modular)

Estado: propuesta verificada contra Open Payments/Rafiki y la test wallet. 16 oct 2026. Entrega del hackathon: 17 oct 11:00.
Contrato con el equipo de Go (fuente de verdad): [`contracts/op-service.openapi.yaml`](../../contracts/op-service.openapi.yaml). Si este documento y el OpenAPI no coinciden, manda el OpenAPI.
Specs para construir el backend (spec-driven): [`backend/specs/`](../specs/README.md). Para implementar, mandan las specs.

Decisiones del equipo: Go no ha definido nada (lo especificamos nosotros) · para el cliente todo es transparente: paga con el QR Bre-B de Puntored desde cualquier banco · se da por hecho que Puntored integra Open Payments y se simula · 1 crédito activo por negocio · total a pagar fijo desde el inicio; lo que se recalcula es el % de cada venta · moneda del comercio, con conversión vía Open Payments si el prestamista es internacional · app móvil y web consumen el mismo backend · Postgres en EC2 con trustservices.com.co · score y cupo desacoplados, visibles para el prestamista, crédito desbloqueado tras X meses de historial · Trust cobra al prestamista (nunca al comercio) un porcentaje de lo que gana, con cada venta · **confirmado: en el flujo del crédito no hay consentimiento del comercio en su wallet**; "Aceptar oferta" es un clic, porque Trust gestiona la cuenta de recaudo desde el alta. El único consentimiento en wallet es el del prestamista al desembolsar.

**Decisiones abiertas (con valor por defecto):**
1. Fórmula de la parte de Trust: prorrateo sobre el cargo fijo (default, recomendado) o literal (% de todo el abono). Ver §1.
2. Plazo esperado en la oferta (`term_months`, 3 por defecto) y regla de recálculo de ±2,5 puntos cada 30 días. Ver §1.
3. `MIN_MONTHS = 3` meses de historial para desbloquear crédito.

## 0. Arquitectura

```
Cliente ──Bre-B / QR Puntored──▶ CUENTA DE RECAUDO TRUST del negocio
                                        │ webhook "pago recibido" (Puntored simulado por op-service)
                                        ▼
Web (app.) / App móvil ──JWT──▶ Trust API (api.trustservices.com.co) ──X-Internal-Api-Key──▶ op-service (Go)
                                        ▲ polling de estados (GET)                                 │ Open Payments
                                        └──────────────────────────────────────────────────────────┘
op-service, desde la cuenta de recaudo: ① abono neto → prestamista  ② parte Trust → wallet de ingresos Trust  ③ resto → cuenta propia del negocio
Prestamista ──consentimiento en su wallet──▶ op-service /v1/interact/finish ──302──▶ app.trustservices.com.co/op/return
```

- Cada negocio tiene dos cuentas. La **de recaudo** la gestiona Trust y su QR es el que exhibe el negocio. La **propia** es del negocio: recibe el desembolso y su parte de cada venta. Si la venta no entra por recaudo, no abona al crédito.
- El negocio usa el QR de Trust desde el primer día, con o sin crédito. Sin crédito, el 100 % va a su cuenta propia. Así se arma el historial que después habilita score y cupo.
- **Trust API** es dueño del negocio y del libro contable. **op-service** es dueño de Open Payments y del simulador de Puntored. Trust nunca ve tokens; guarda IDs y URLs para la trazabilidad.

## 1. Reglas de dinero

- Montos en enteros (unidades mínimas) + asset_code + asset_scale de la wallet. Porcentajes en puntos básicos (15 % = 1500). Cero floats, columnas BIGINT, cálculos en Python. Se guarda en UTC y se agrupa en America/Bogota.
- El crédito va en la moneda del comercio. `total_a_pagar = principal + cargo_fijo`. El cargo sale de la oferta (fee_bps sobre el principal), se congela al crear el crédito y **nunca cambia**.
- Por venta con crédito `active` (solo ese estado abona): `abono = min(floor(venta × pct_vigente / 10000), saldo)`. El negocio recibe `venta − abono`. Las tres patas suman siempre la venta.
- **Parte de Trust, prorrateada sobre la ganancia** (verificado con 20.000 créditos simulados: las patas suman exacto, el neto nunca es negativo, Trust recibe exactamente su parte del cargo y el prestamista recupera siempre el principal):
  `T(r) = floor(r × cargo × trust_share_bps / (total × 10000))`, `parte_trust = T(pagado_después) − T(pagado_antes)`, `neto_prestamista = abono − parte_trust`.
  Alternativa literal (DECISIÓN ABIERTA del equipo): `parte_trust = floor(abono × trust_share_bps / 10000)`. Riesgo: con cargo 1 % y parte 20 %, Trust se lleva 20 veces el cargo y el prestamista pierde principal. Ambas son una sola función en `policies/split.py`.
- **Asignado vs entregado**: el saldo baja cuando la venta se asigna (la plata ya está en recaudo). La entrega a cada wallet se sigue aparte. El crédito guarda `allocated_total` y `delivered_total`; el portafolio muestra "recibido" y "en camino".
- **Patas en cero o diminutas**: no se crea la pata si vale 0. La parte de Trust se acumula en `trust_pending` y se transfiere cuando pasa `TRUST_LEG_MIN` o cuando el crédito termina. Open Payments rechaza montos de 0 y montos que la comisión deja en 0.
- **Recalculo del %** (el total no cambia). Al crear el crédito se definen: `pct_inicial = oferta.pct`, `pct_max = solicitud.pct` (el techo que declaró el negocio; si el crédito sale de una oferta sin solicitud: `oferta.pct + REPRICE_MAX_DELTA_BPS`, 500 por defecto), `pct_min = max(500, pct_inicial − 500)`, `term_months` (de la oferta, 3 por defecto) y `minimo_mensual = ceil(total / term_months)`. Hay un periodo cada 30 días desde `activated_at`. Al cerrar el periodo:
  - objetivo = `min(minimo_mensual, saldo al inicio del periodo)`;
  - si lo asignado en el periodo es menor que el objetivo, `pct += 250` (con tope en pct_max) y `missed_periods += 1`;
  - si es al menos el 125 % del objetivo, `pct −= 250` (con piso en pct_min);
  - con 2 o más periodos perdidos el crédito se marca "atrasado" (visible al prestamista y notificado).
  El nuevo % aplica a las ventas siguientes, bajo el lock del crédito, y cada abono guarda `pct_applied`.
- **Cupo no rotativo**: `cupo_disponible = max(0, cupo_total − principal del crédito no terminado)` (6M − 2M = 4M ✔). Se reserva desde el primer estado del crédito.
- **Internacional**: las salidas desde recaudo fijan lo que se envía (debit) en la moneda del comercio; el prestamista recibe el monto convertido (`receive_amount`). El desembolso fija lo que se recibe: el negocio recibe exactamente el principal y el prestamista paga el equivalente. El crédito se contabiliza por el debit, en la moneda del comercio.

## 2. Políticas desacopladas (`app/policies/`): funciones puras, versionadas, configurables por env

| Política | Firma | Default v1 |
|---|---|---|
| scoring | `assess(features) -> CreditAssessment{score 0–1000, grade, eligible, unlocks_at, cupo_total, reasons[]}` | Elegible si `active_months ≥ MIN_MONTHS` (3), donde active_months = meses calendario con ≥ N ventas por recaudo, contados desde la primera venta (no desde el alta). Score = 1000 × (0,35 volumen + 0,25 consistencia + 0,15 días activos + 0,10 antigüedad + 0,15 historial de pagos), con historial neutro si nunca ha tenido crédito. Grados: A ≥ 750, B ≥ 600, C ≥ 450, D. Cupo = min(TOPE, redondeo(prom. ventas mensuales 3m × 15 % × 2,5 × factor A 1 / B 0,75 / C 0,5 / D 0)). 16M/mes con grado A → 6M ✔ |
| matching | `fits(request, offer, cupo) -> Proposal \| None` | Los rangos se cruzan, overlap_min ≤ cupo, oferta.pct ≤ solicitud.pct. Monto = punto medio del cruce, redondeado al paso y recortado al cupo |
| terms | `build_terms(principal, offer, request, config) -> Terms` | Cargo, total, pct inicial/min/max, plazo, mínimo mensual, trust_share_bps |
| split | `split_sale(amount, loan_state) -> {merchant, lender, trust}` | Fórmulas de §1 |
| repricing | `review_period(loan, allocated_in_period) -> Decision` | Regla de §1 |

`features.py` saca los insumos de Postgres; las políticas no tocan la base. Cada evaluación queda en `credit_assessments` (versión, insumos, resultado). La solicitud y el crédito guardan el `credit_assessment_id` con el que se decidió.

## 3. Módulos y endpoints de la Trust API (`/api/v1`; ★ = MVP de la demo)

**users**
- POST /auth/register
- POST ★ /auth/login (JWT para web y móvil)
- GET ★ /me
- PATCH /me (perfil, "Mi rango" del prestamista)
- PUT ★ /me/payout-wallet → Go `GET /v1/wallet-addresses`. Para el negocio, el asset debe ser el asset de mercado `MARKET_ASSET_CODE` (COP con escala 2; 422 si no) y, si ya tiene cuenta de recaudo, ser el de esa cuenta. Responde 409 si hay un crédito no terminado.

**recaudo**
- GET ★ /me/collection-account (QR, wallet, estado)
- POST /me/collection-account → Go `POST /v1/collection-accounts {external_ref: merchant:{id}, display_name: business_name, asset_code: asset de la wallet propia}`. Exige wallet propia (422 `wallet_required` sin ella).

**scoring**
- GET ★ /me/credit-profile (score, grado, elegible, fecha de desbloqueo, cupo total/usado/disponible, razones)
- GET ★ /merchants/{id}/credit-profile (vista del prestamista: solo si hay una solicitud abierta, un match o un crédito con él; muestra rangos de ventas, no cifras)

**marketplace**
- POST ★ /loan-requests {min, max, pct_bps}
- GET ★ /loan-requests?scope=mine|market (market: negocio, ciudad, score, grado, cupo)
- GET /loan-requests/{id}
- POST /loan-requests/{id}/cancel
- POST ★ /loan-offers {min, max, pct_bps, fee_bps, term_months}
- GET ★ /loan-offers?scope=mine|market&fit_min&fit_max&pct_bps (`count` alimenta "3 ofertas encajan")
- GET /loan-offers/{id}
- POST /loan-offers/{id}/cancel
- GET ★ /matches (se calculan al vuelo)

**loans**
- POST ★ /loans/preview {request_id?, offer_id?, amount?} → términos completos sin crear nada ("Ver condiciones completas")
- POST ★ /loans {request_id?, offer_id?, amount?}. Transacción: revalida elegibilidad y cupo, pasa la solicitud a `matched` y la oferta a `taken`, congela términos, wallets y assessment.
- POST /loans/{id}/accept
- POST /loans/{id}/reject
- POST ★ /loans/{id}/disburse {client: web|mobile} → Go `POST /v1/payments` (Idempotency-Key `loan:{id}:disb:{disbursement_attempt}`) → {authorization_url}. Con el 201 el crédito pasa a `disbursing` y guarda op_disbursement_payment_id. Si ya está `disbursing` con el pago en awaiting_authorization, devuelve ese mismo authorization_url sin llamar a Go. Solo un estado terminal de Go (failed/rejected/expired) lo devuelve a `pending_disbursement` y sube el intento.
- POST /loans/{id}/cancel (solo en pending_*)
- GET ★ /loans
- GET ★ /loans/{id} (avance asignado y entregado, pct vigente, periodo actual: asignado vs objetivo, próxima revisión, next_action)
- GET /loans/{id}/pct-history

Estados del crédito:
- `pending_acceptance`: el prestamista fondeó una solicitud y el negocio debe aceptar, porque el cargo lo puso el prestamista.
- `pending_disbursement`: el negocio tomó una oferta y entra directo aquí; expira a las 48 h y entonces se reabren la solicitud y la oferta.
- `disbursing` → `active` → `paid`.
- `cancelled`, `rejected` o `expired`: solo antes de desembolsar.

La aceptación del prestamista es su consentimiento en la wallet. El índice único parcial (1 crédito no terminado por negocio) cubre todos los estados no terminales.

**sales**
- POST ★ /webhooks/puntored/payments (HMAC, sin JWT). Ver §5.
- GET ★ /sales/today?since= (feed "En vivo": ventas, para ti, abonado, número de ventas, regla vigente, una fila por venta con su división)
- GET /sales
- GET /sales/{id}

**repayments**
- GET ★ /repayments?loan_id&from&to (para el prestamista: bruto, parte Trust, neto, recibido, asset)
- GET /repayments/{id} (traza: venta → abono → transferencias Open Payments)

**dashboard**
- GET ★ /dashboard/merchant (cupo, crédito activo, ventas de hoy, ofertas que encajan, avisos sin leer)
- GET ★ /dashboard/lender (total prestado, abonos de hoy por hora, mi rango, créditos con "+hoy", últimos abonos en bruto con desglose de la parte Trust; totales agrupados por asset)

**admin**
- GET /admin/dashboard (ingresos de Trust por asset, fondos retenidos)
- POST /admin/payouts/{id}/retry
- POST /admin/collection-accounts/{id}/revoke

**notifications**
- GET /notifications
- POST /notifications/read-all

**waitlist**
- POST ★ /waitlist (lo que ya envía la landing; responde 201 si es nuevo y 200 si ya existía, nunca 409; con CORS)

**platform/dev**
- GET ★ /health
- GET /config
- POST ★ /dev/seed (ventas retroactivas con source='seed')
- POST ★ /dev/sales/simulate → simulador de Go
- POST /dev/loans/{id}/close-period (fuerza la revisión del % en la demo)

**integrations/op**: `OpClient` (Protocol) con `HttpOpClient` y `FakeOpClient`, elegido con `OP_CLIENT=fake|http`. El fake permite avanzar sin depender de Go.

## 4. Contrato op-service (Go) — lo especificamos nosotros

Convenciones:
- Header `X-Internal-Api-Key`.
- `Idempotency-Key` obligatorio en todo POST. Solo se guardan las respuestas 2xx y las 4xx definitivas; misma clave + mismo cuerpo (JSON canónico) = misma respuesta con su status original y `Idempotent-Replayed: true`; misma clave + otro cuerpo = 409. Los 5xx y timeouts no se guardan: se reintenta con la misma clave. Un error en el POST = no se creó nada; lo que falle después aparece como `failed` + `failure_code` en el GET.
- Montos como `{value: "entero", asset_code, asset_scale}`. Error como `{error: {code, message, retryable}}`.
- Todo recurso lleva `external_ref`. Fechas en RFC 3339 UTC. Nunca se devuelven tokens.

| # | Endpoint | Cuerpo → respuesta | Lo usa Trust en |
|---|---|---|---|
| 1 | GET /v1/wallet-addresses?url= | → {url, public_name, asset_code, asset_scale} | PUT /me/payout-wallet |
| 2 | POST /v1/collection-accounts · GET ?external_ref= | {external_ref: merchant:{id}, display_name, asset_code} → asigna una wallet del **pool ya autorizado** → {id, wallet_address, qr_payload, asset_code, asset_scale, status: active}; 409 pool_exhausted. `qr_payload` = `https://pay.trustservices.com.co/qr/{id}`, una página "Pagar con Bre-B (simulado)" que pide el monto y llama al simulador (#8). NO la wallet cruda: si alguien le paga directo, no hay webhook ni división | POST /me/collection-account |
| 3 | GET /v1/collection-accounts/{id} · DELETE (P1, revoca el grant) | → {status: active\|revoked, ledger_balance informativo} | worker / admin |
| 4 | POST /v1/transfers | {collection_account_id, receiver_wallet_address, debit_amount, description, external_ref} → 201 {id, status: processing} (asíncrono, responde sin esperar turno; el resultado se ve con GET) | worker: cada pata |
| 5 | GET /v1/transfers/{id} | → status processing\|completed\|failed, debit/sent/receive_amount, failure_code (reintentables: insufficient_balance\|receiver_unreachable\|upstream_error\|internal; terminales: invalid_amount\|amount_too_small\|grant_revoked), retryable, incoming_payment_url, outgoing_payment_url, receiver_completed_at | worker |
| 6 | POST /v1/payments | {sender_wallet_address, receiver_wallet_address, receive_amount, description, external_ref, return_url} → {id, status: awaiting_authorization, authorization_url, authorization_expires_at, debit_amount} | POST /loans/{id}/disburse |
| 7 | GET /v1/payments/{id} | → awaiting_authorization\|processing\|completed\|failed\|rejected\|expired, montos, failure_code, outgoing_payment_url | worker |
| 8 | POST /v1/sim/puntored/qr-payments | {collection_account_id, amount, payer_bank?} → {id}; paga desde la wallet "Cliente demo" al recaudo (fixed-receive con quote; no usa el camino de las transferencias) y luego dispara el webhook. Solo red interna o flag DEV | POST /dev/sales/simulate |
| 9 | GET /v1/sim/puntored/qr-payments?after= | → reenvío de webhooks perdidos | reconciliación |
| 10 | GET /v1/health | → {status, client_wallet_address} | GET /health |
| — | GET /v1/interact/finish/{attempt} (navegador) | Verifica hash, continúa el grant, ejecuta y hace 302 a return_url | Trust solo arma el return_url |
| — | GET /v1/admin/pool-wallets · POST /v1/admin/pool-wallets/{id}/authorize (interno) | El operador de Trust aprueba una vez el grant sin límites de cada wallet del pool (y de "Cliente demo") | Preparación antes de la demo |
| — | GET /qr/{id} · POST /qr/{id}/pay (público) | Página "Pagar con Bre-B (simulado)": pide el monto y hace lo mismo que #8, con tope y rate limit | Lo que escanea el jurado |

**Webhook de Puntored (simulado) → Trust**: `POST https://api.trustservices.com.co/api/v1/webhooks/puntored/payments`
- Header: `X-Puntored-Signature: t=<unix>,v1=<hex HMAC-SHA256(secreto, t + "." + body)>`.
- Cuerpo: `{event_id, type: "qr_payment.received", occurred_at, data: {collection_account_id, collection_wallet_address, amount: {value, asset_code, asset_scale}, bre_b_reference, payer_bank, incoming_payment_url}}`.
- Si no recibe 2xx, Go reintenta a los 1 s, 5 s, 30 s y 2 min. Es el mismo endpoint que usaría Puntored real.

**Lo que Go resuelve por dentro** (verificado):
1. Usuarios de la test wallet, todos con KYC sandbox:
   - "Trust": wallet cliente con llave Ed25519 de desarrollador + pool de 3–5 wallets de recaudo en COP (escala 2). Ojo: todas las wallets de un usuario y asset comparten un mismo saldo, así que el saldo real por negocio lo lleva el libro de Trust.
   - "Trust Ingresos": recibe la parte de Trust.
   - "Cliente demo": paga en el simulador; tiene su propio grant permanente, que el operador aprueba con la sesión de "Cliente demo".
   - Comercio y prestamista: uno o varios usuarios de prueba.
2. Por cada wallet del pool, un grant de salida **sin límites** (identifier = la wallet, actions create/read). Lo aprueba una vez el operador; el grant no expira y su token rota cada 10 min. Hay que guardar el token nuevo **y** el nuevo manage URL de forma atómica.
3. Transferencias: el POST solo valida, guarda y responde 201 `processing`. Un worker por grant (cada wallet del pool y "Cliente demo") toma su cola FIFO y, bajo un mutex de ese grant:
   - rota el token si venció;
   - crea el incoming payment en el receptor al sacarla de la cola, **sin monto** y con expiresAt de +15 min, usando un grant no interactivo con create/read/complete;
   - crea el outgoing payment `{incomingPayment, debitAmount}` con el token de recaudo (plan B: quote con debitAmount + quoteId);
   - hace poll hasta `failed`, o `sentAmount == debitAmount`, o `receivedAmount` del incoming del receptor ≥ `receiveAmount` del outgoing;
   - **`IncomingPayment.Complete` en el receptor (obligatorio: sin eso la wallet receptora no acredita)** → completed.
   - No espera a que termine una transferencia para crear la siguiente. Cualquier rechazo de Open Payments queda como `failed` + `failure_code`. Go nunca reintenta ni ajusta montos: el reintento es un POST nuevo de Trust.
4. Desembolso:
   - incoming payment en la cuenta propia del negocio con incomingAmount = principal;
   - quote en la wallet del prestamista;
   - grant interactivo (limits.debitAmount = quote.debitAmount, receiver = ese incoming);
   - al volver, re-cotizar si pasaron 5 min;
   - outgoing payment desde la quote.
5. Finish:
   - hash = `base64(sha256(client_nonce \n interact.finish \n interact_ref \n authServer))`, en base64 estándar con padding; comparar también la forma sin padding;
   - authServer exacto, con tenant y sin slash;
   - `result=grant_rejected` llega sin hash;
   - esperar `continue.wait` (5 s);
   - consentimiento abandonado → expired a los 10 min;
   - return_url limitado a la allowlist (`https://app.trustservices.com.co/`).
6. authServer y resourceServer se usan tal como los devuelve la wallet; nunca se construyen.

## 5. Webhook de Puntored (lo más delicado de Trust)
En una sola transacción, sin llamar a Go adentro:
1. Verificar el HMAC en tiempo constante sobre el body crudo y que la marca de tiempo esté dentro de ±300 s. Se acepta una lista de secretos, para poder rotarlos.
2. `INSERT webhook_events ON CONFLICT (event_id) DO NOTHING`. Además, UNIQUE(collection_account_id, bre_b_reference) frena reenvíos con otro event_id.
3. Cuenta desconocida, o wallet o asset que no cuadra: responder 200, marcar `unmatched` y alertar (un 4xx haría que Puntored reintente para siempre). Cuenta no activa: registrar la venta y dejar las patas en `held`.
4. `SELECT … FOR UPDATE` sobre la cuenta de recaudo y luego sobre el crédito `active` (si existe).
5. `split_sale` → insertar la venta, el abono (pct_applied y parte Trust) y las patas con valor > 0, con el receptor congelado. Si el saldo llega a 0, el crédito pasa a `paid` y el resto de la venta va al negocio.
6. COMMIT y responder 200. El worker toma las patas con `FOR UPDATE SKIP LOCKED`.
Fuera de alcance explícito: reversos y contracargos de Bre-B (`qr_payment.reversed` se guarda y se ignora).

## 6. Worker de Trust
Un proceso (`uvicorn --workers 1`); corre cada 2 s y es idempotente.
1. Patas `pending` → POST /v1/transfers con Idempotency-Key `payout:{id}:{intento}`, en orden prestamista → negocio → Trust (si pasó el mínimo). El body se arma solo con datos guardados en la pata (hora de la venta, no now()) para que un reintento con la misma clave mande el mismo cuerpo. Respuestas: 201 → `processing`. 5xx, timeout o `409 request_in_progress` → mismo intento, misma clave. 422 → `failed_final` + alerta. `409 collection_account_not_active` → cuenta `revoked` y sus patas pendientes a `held`.
2. Patas `processing` → GET. Si `completed`: `sent_total += debit_amount`. Si `failed`: `sent_total += sent_amount`; si es `retryable` y queda resto, nuevo intento (`intento + 1`) con `debit_amount = monto − sent_total`, con backoff de 10 s, 1 min y 5 min (máximo 5); si no, `failed_final`: la plata se queda en recaudo y aparece en "fondos retenidos". `grant_revoked` → cuenta `revoked` y patas a `held`. `delivered_total` del crédito suma lo entregado de las patas del prestamista y de Trust.
3. Créditos en `disbursing` → GET /v1/payments. Si `completed`, pasan a `active` (activated_at, inicio de periodo, aviso). Si `failed`/`rejected`/`expired`, vuelven a `pending_disbursement`.
4. Créditos pendientes vencidos → `expired`, y se reabren la solicitud y la oferta.
5. Al arrancar y cada 30 s: reconciliación con `GET /v1/sim/puntored/qr-payments?after={cursor}`. Cada item `completed` pasa su `webhook_event` al mismo handler del webhook (sin HMAC, dedup por event_id) y el cursor se guarda en `op_cursors` en la misma transacción.
6. Diario: cerrar periodos (repricing). El score se calcula al leer, con caché diaria.

## 7. Tablas (Postgres)
| Tabla | Contenido |
|---|---|
| users | role, nombres, business_name, ciudad, payout wallet + asset, mi rango |
| collection_accounts | merchant_id, op_id, wallet, qr, asset, status, ledger: entradas y salidas |
| credit_assessments | versión, insumos, resultado |
| loan_requests | rango, pct, status, credit_assessment_id |
| loan_offers | rango, pct, fee_bps, term_months, status |
| loans | Términos congelados, wallets congeladas, credit_assessment_id, pct vigente/min/max, minimo_mensual, period_start, next_review_at, missed_periods, allocated_total, delivered_total, trust_earned, trust_pending, status, expires_at, op_disbursement_payment_id, disbursement_attempt, timestamps |
| loan_pct_changes | Historial de cambios del % |
| sales | UNIQUE puntored_event_id y (collection_account_id, bre_b_reference); monto, asset, paid_at, source |
| repayments | Asignación por venta: abono, parte Trust, neto, pct_applied; UNIQUE sale_id |
| payouts | sale_id, leg merchant\|lender\|trust, receptor congelado, monto, sent_total, status (pending\|processing\|completed\|failed_final\|held\|skipped), intento, op_transfer_id, montos y URLs de OP; UNIQUE (sale_id, leg) |
| webhook_events | Bandeja de entrada y dedup |
| op_cursors | name, value (cursor de reconciliación del simulador) |
| notifications | |
| waitlist | |
| events | Auditoría |

## 7.1 Variables de entorno de Trust
`DATABASE_URL` · `JWT_SECRET` · `OP_CLIENT=fake|http` · `OP_SERVICE_BASE_URL=http://op-service:8080` · `OP_SERVICE_API_KEY` (= `INTERNAL_API_KEY` de Go) · `OP_HTTP_TIMEOUT_SECONDS=10` · `PUNTORED_WEBHOOK_SECRETS` (separados por comas; uno igual a `TRUST_WEBHOOK_SECRET` de Go) · `TRUST_REVENUE_WALLET_ADDRESS` (wallet "Trust Ingresos", receptor de la pata Trust; se resuelve al arrancar con `GET /v1/wallet-addresses`) · `TRUST_SHARE_BPS` · `TRUST_LEG_MIN` · `MARKET_ASSET_CODE=COP` y `MARKET_ASSET_SCALE=2` (un solo asset en el MVP; la test wallet soporta COP, verificado por el equipo) · `MIN_MONTHS=3` · `SCORING_POLICY=v1` · `APP_WEB_URL=https://app.trustservices.com.co` · `CORS_ORIGINS` (web y landing).

## 8. Despliegue EC2
- docker compose con caddy (TLS automático), trust-api, op-service y postgres. Hay dos bases, `trust` y `opservice`, cada servicio con la suya.
- Dominios:
  - trustservices.com.co → landing
  - app. → web; también la página `/op/return`
  - api. → Trust API: web, móvil y webhook de Puntored
  - pay. → Go: público **solo** `/v1/interact/finish/*` y la página del QR simulado; lo demás va por la red interna de docker
- Retorno a la app móvil: la página web `/op/return?client=mobile` redirige a `trust://loans/{id}`, y la app hace polling al volver a primer plano.

## 9. Orden sugerido (hasta la entrega)
1. 30–45 min, los dos equipos: congelar este contrato en `contracts/op-service.openapi.yaml` con JSON de ejemplo. Go: usuarios de la test wallet, llave, pool autorizado y el spike de transfer (con Complete) + desembolso.
2. Trust: core, modelos, seed, login, FakeOpClient.
3. Webhook + split + patas + worker.
4. Créditos: preview, crear desde oferta, desembolso.
5. Marketplace + scoring.
6. Dashboards con la forma de COPY.md §13.
7. Cambiar el fake por Go real, probar de punta a punta, desplegar en EC2, ensayar y grabar el video.

Si no alcanza, recortar en este orden: crédito iniciado por el prestamista (pending_acceptance), registro (usar usuarios sembrados), notificaciones (derivarlas de events), pct-history, admin, FX (un solo asset), repricing automático (solo `/dev/loans/{id}/close-period`).

## 10. Backlog
KYC real · tipos de prestamista · mora y cobranza · alertas si caen las ventas por el QR de Trust (señal de que el negocio lo evita) · detectar pagos reales al QR (list-all) · pagos al negocio por lote · webhooks de Go en vez de polling · Alembic · varios créditos por negocio · Ley 1581 (consentimiento para compartir el perfil).
