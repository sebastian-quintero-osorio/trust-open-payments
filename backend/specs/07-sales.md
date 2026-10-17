# 07 · Ventas: webhook de Puntored y división

Estado: ☑ hecho · Fase: 1 · Dueño: Claude · Carpeta: `app/modules/sales/`

## Objetivo
Recibir cada pago Bre-B (webhook de Puntored, simulado por op-service), dividirlo en una sola transacción (venta → abono → patas) y mostrarle al comercio sus ventas del día con su división (COPY 13c `division-en-vivo`).

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `POST /api/v1/webhooks/puntored/payments` | — (HMAC, sin JWT) | Header `X-Puntored-Signature: t=<unix>,v1=<hex>` + body `PuntoredPaymentEvent` (OpenAPI) | 200 `{"status": "processed" \| "duplicate" \| "unmatched" \| "ignored"}` | 401 `invalid_signature`, 401 `signature_expired`, 400 `bad_request` (JSON ilegible o sin `event_id`/`type`) |
| ★ | `GET /api/v1/sales/today?since=` | merchant | `since` opcional (ISO, el `next_since` anterior) | Feed del día (abajo) | 422 `validation_error` |
| | `GET /api/v1/sales?from=&to=` | merchant | Fechas locales `YYYY-MM-DD` inclusivas + paginación | `{"items": [Fila], "next_offset"}` | 422 `validation_error` |
| | `GET /api/v1/sales/{sale_id}` | merchant (dueño), admin | — | `{"sale", "repayment" \| null, "payouts": [...]}` | 403 `forbidden`, 404 `not_found` |

`Fila` = `{sale_id, paid_at, amount, merchant_amount, abono_amount, payout_status}` (Money en los montos).

`GET /sales/today` (hoy 16 oct, crédito al 15 %):
```json
{"date": "2026-10-16", "live": true,
 "summary": {"sales_total": {"value": "61200000", "asset_code": "COP", "asset_scale": 2}, "sales_count": 14,
             "merchant_total": {"value": "52020000", "asset_code": "COP", "asset_scale": 2},
             "abonado_total": {"value": "9180000", "asset_code": "COP", "asset_scale": 2}},
 "rule": {"merchant_bps": 8500, "lender_bps": 1500},
 "wallet_today": {"value": "52020000", "asset_code": "COP", "asset_scale": 2},
 "rows": [{"sale_id": "…", "paid_at": "2026-10-16T15:42:00Z",
           "amount": {"value": "4800000", "asset_code": "COP", "asset_scale": 2},
           "merchant_amount": {"value": "4080000", "asset_code": "COP", "asset_scale": 2},
           "abono_amount": {"value": "720000", "asset_code": "COP", "asset_scale": 2}, "payout_status": "in_progress"}],
 "next_since": "2026-10-16T15:42:01.204312Z"}
```

`GET /sales/{sale_id}`: `sale {id, amount, paid_at, bre_b_reference, payer_bank, incoming_payment_url, source, status}` · `repayment {id, loan_id, abono, trust_cut, lender_net, pct_applied_bps} | null` · `payouts [{id, leg, amount, sent_total, status, attempt, receive (Money o null), incoming_payment_url, outgoing_payment_url, completed_at, failure_code}]`.

## Reglas
- **R1. Firma.** `raw = await request.body()` antes de parsear. El header son pares `k=v` separados por coma; exige `t` entero y al menos un `v1`. Válida si para algún secreto de `PUNTORED_WEBHOOK_SECRETS` y algún `v1`: `hmac.compare_digest(hmac_sha256(secreto, f"{t}.".encode() + raw).hexdigest(), v1)`. Sin header, mal formado o sin coincidencia → 401 `invalid_signature`; firma válida con `|clock.now() − t| > 300 s` → 401 `signature_expired`. Con 401 no se escribe nada.
- **R2. Router.** Declara `x_puntored_signature: str | None = Header(None)` y no usa un modelo Pydantic para el body (el schema del YAML se publica con `openapi_extra`); nunca responde 422. Firma válida → `json.loads(raw)` → `process_puntored_event(db, event, "puntored")` → commit → 200 `{"status": result.status}`. JSON que Postgres no puede guardar (NaN/Infinity, `\u0000`) o anidado a más de 64 niveles (`RecursionError`) → 400 `bad_request`. Excepción inesperada → rollback y 500 (Go reintenta; el dedup lo hace seguro). La ruta no usa JWT ni depende de CORS.
- **R3. `process_puntored_event(db, event, source) -> WebhookResult(status, sale_id | None)`.** No hace commit (lo hace quien llama: router o worker) y no llama a Go. Pasos en orden:
  1. `INSERT webhook_events (event_id, source, type, payload, received_at) ON CONFLICT (event_id) DO NOTHING` (el `status` final se escribe al terminar). Si no insertó → `duplicate` y no toca nada más.
  2. `type != "qr_payment.received"` → `ignored`.
  3. Valida `data` (si falta un campo, el monto no es positivo o no cabe en `bigint` —también con más de 19 dígitos—, o `occurred_at` no tiene zona o desborda al pasarlo a UTC → `invalid_payload`) y luego busca la cuenta por `op_collection_account_id = data.collection_account_id`, con `FOR UPDATE`. Si `data` es inválida, la cuenta no existe (`unknown_account`), `wallet_address != data.collection_wallet_address` (`wallet_mismatch`) o el asset del monto no es el de la cuenta (`asset_mismatch`) → `unmatched` con ese `error` + `record_event`. Nunca 4xx.
  4. Bloquea (`FOR UPDATE`, después de la cuenta) el crédito no terminado del negocio. Solo abona si `status == active`.
  5. Ya existe venta con (`collection_account_id`, `bre_b_reference`) → `duplicate`; el webhook_event queda `ignored` con `error = duplicate_reference`.
  6. Inserta `sales`: `puntored_event_id`, `bre_b_reference`, `amount`, `payer_bank`, `incoming_payment_url`, `paid_at = occurred_at`, `source = live`, `status = processed` (o `held` si la cuenta no está `active`).
     - 6b. `trust_pending` es plata de la cuenta donde se acumuló: la de la venta del último abono del crédito con `trust_cut > 0` (mayor `allocated_after`). Si hay crédito active, `trust_pending > 0` y esa cuenta no es la de esta venta (la anterior se revocó y el negocio tiene otra, o llega una venta `held` a la revocada), lo pendiente sale ya como pata `trust` de esa venta anterior (que no tiene pata `trust`), `held` si esa venta o su cuenta ya no está activa, con evento `created` `{reason: "trust_pending_account_changed"}`, y `trust_pending = 0`. Así ninguna cuenta debita lo que no recibió: por cuenta, Σ patas + pendiente acumulado en ella = `ledger_in`. No bloquea la otra cuenta (orden cuenta → crédito): si la revocan a la vez, 08 R4 retiene la pata.
  7. `s = split_sale(amount, SplitLoanState(pct_bps, total_repayable, allocated_total, fee_amount, trust_share_bps) si hay crédito active, si no None, policy=TRUST_CUT_POLICY)`.
  8. Si `s.abono > 0`: inserta `repayments` (`pct_applied_bps = loan.pct_bps`, `allocated_before = loan.allocated_total`, `allocated_after = s.allocated_after`) y en el crédito: `allocated_total = s.allocated_after`, `trust_earned += s.trust_cut`, `trust_pending += s.trust_cut`.
  9. Pata Trust: `t = trust_pending` si `trust_pending > 0` y (`trust_pending ≥ TRUST_LEG_MIN` o `s.paid_off`), si no `t = 0`; `trust_pending −= t` (tras 6b, todo lo pendiente es de esta cuenta).
  10. Inserta las patas con monto > 0 (R4) y un `record_event` por pata.
  11. `collection_accounts.ledger_in += amount`.
  12. Si `s.paid_off`: crédito `paid`, `paid_at = now`, `record_event`, `notify loan_paid` al negocio y al prestamista.
  13. webhook_event `processed` con `sale_id` y `processed_at`. Si `NOTIFY_SALE_SPLIT` y la venta es `processed` (no `held`, como dice 14): `notify sale_split` al negocio.
- **R4. Patas** (receptor congelado, asset de la venta, `attempt = 1`, `current_debit_amount = amount`, `status = pending`, o `held` si la venta es `held`): `lender` = `s.lender_net` → `loan.lender_wallet_url` · `merchant` = `s.merchant_amount` → `loan.merchant_payout_wallet_url` si hay crédito active, si no `users.payout_wallet_url` · `trust` = `t` → `TRUST_REVENUE_WALLET_ADDRESS` (si está vacío, la pata `trust` queda `held` con `receiver_wallet_url = null` y un log de error). `loan_id` = crédito active (null en `merchant` sin crédito). Negocio sin payout wallet → pata `merchant` `held` con `receiver_wallet_url = null`. La pata Trust puede sumar `trust_pending` de ventas anteriores **de la misma cuenta** (6b); `lender + merchant + trust_cut = amount` siempre.
- **R5. Venta `held`** (cuenta revocada): se divide y se asigna al crédito igual (la plata ya está en recaudo); solo las patas quedan `held`. Liberarlas es de 16-admin.
- **R6. Descripción congelada** (≤ 200, con `{hora}` = `clock.format_hora(paid_at)`, formato `10:42 a. m.`, nunca `now()`): lender `Abono venta {hora} · {negocio}` · merchant `Venta {hora} · {negocio}` · trust `Parte Trust venta {hora} · {negocio}`.
- **R7. Estado de entrega agregado** (`payout_status` de una venta; 12 y 13 usan el mismo vocabulario): `failed` si alguna pata `failed_final`; si no `held` si alguna `held`; si no `in_progress` si alguna `pending`/`processing`; si no `completed` (también sin patas). Las patas `skipped` se ignoran.
- **R8. Cifras del comercio.** `abono_amount` es el abono **bruto** (incluye la parte Trust: "15 % a tu crédito"). `merchant_amount = amount − abono` (abono del repayment, 0 si no hay; así cuentan también las ventas `seed`). `rule` = `{merchant_bps: 10000 − pct_bps, lender_bps: pct_bps}` del crédito `active`, o `{10000, 0}`.
- **R9. `/sales/today`.** Día `today_local()` con `local_day_bounds`, ventas del negocio por `paid_at` (todas las fuentes). `summary` sobre todo el día; `rows` (orden `paid_at` desc) solo con `created_at > since` si viene `since`. `next_since` = el mayor entre el máximo `created_at` de las ventas del día y el `since` recibido (el cursor nunca retrocede; null si no hay ninguno), serializado con precisión de microsegundos (isoformat completo, con `Z`). `since` sin zona horaria, ilegible o que desborda al pasarlo a UTC (p. ej. `0001-01-01T00:00:00+14:00`) → 422 `validation_error`. `wallet_today = merchant_total` (la app lo rotula "En tu wallet Puntored hoy"). `live` siempre `true`.
- **R10. `today_summary(db, merchant_id, day) -> TodaySummary(sales_total, sales_count, merchant_total, abonado_total)`** en `sales/service.py` calcula `summary`; 13-dashboard usa la misma función.
- **R11.** `/sales` y `/sales/{sale_id}`: el negocio solo ve sus ventas (ajena → 403); en el detalle ve las tres patas. `GET /sales`: orden `paid_at` desc, luego `id`; sin `from`/`to`, todo el historial (todas las fuentes); fecha mal formada o `from` > `to` → 422 `validation_error`.

## Datos
- Lee: `users`, `collection_accounts`, `loans` · Escribe: `webhook_events`, `sales`, `repayments`, `payouts`, `loans` (`allocated_total`, `trust_earned`, `trust_pending`, `status`, `paid_at`), `collection_accounts` (`ledger_in`), `events`, `notifications`.

## Depende de
- `05-policies` (`split_sale`) · `06-recaudo` (cuentas de recaudo) · `11-loans` (créditos `active`; el paso a `paid` lo hace esta spec, ver máquina de estados en 01) · `14-notifications` (`notify` y textos) · `00-core` (`clock`, `record_event`, `money`).

## Criterios de aceptación
Crédito tipo (CT): principal $2.000.000, `fee_bps` 1000 (cargo $200.000, total $2.200.000), `trust_share_bps` 2000, `pct_bps` 1500, `allocated_total` $0, `trust_pending` $0, `active`. Con CT, `T(r) = floor(r / 55)` con `r` en centavos. Config por defecto (`TRUST_LEG_MIN` $100, `TRUST_CUT_POLICY` prorated).
- [x] **AC-1.** Dado CT, cuando llega un webhook firmado con una venta de $48.000, entonces 200 `processed`, 1 venta, repayment (abono $7.200, trust_cut $130,90, lender_net $7.069,10, pct 1500, allocated $0 → $7.200), patas `pending` lender $7.069,10 / merchant $40.800 / trust $130,90, `ledger_in` $48.000, `trust_earned` $130,90, `trust_pending` $0 (→ `test_ac1_webhook_splits_sale`)
- [x] **AC-2.** Dado un header ausente o firmado con un secreto ajeno → 401 `invalid_signature`; firmado bien con `t = now − 301` → 401 `signature_expired`; en ambos casos `webhook_events` vacío (→ `test_ac2_rejects_bad_signature`)
- [x] **AC-3.** Dado `PUNTORED_WEBHOOK_SECRETS="viejo,nuevo"` y un body con espacios y claves en otro orden, cuando se firma con `nuevo` sobre esos bytes exactos, entonces 200 (→ `test_ac3_secret_rotation_raw_body`)
- [x] **AC-4.** Dado el mismo `event_id` dos veces, la segunda da `duplicate`; con otro `event_id` y el mismo `bre_b_reference` también `duplicate` (webhook_event `ignored`); queda 1 venta con 3 patas (→ `test_ac4_dedup`)
- [x] **AC-5.** Dada una cuenta desconocida, una wallet que no cuadra o un monto en USD → 200 `unmatched`, webhook_event `unmatched` con su `error`, sin venta (→ `test_ac5_unmatched`)
- [x] **AC-6.** Dado `type = qr_payment.reversed` → 200 `ignored`, sin venta (→ `test_ac6_ignores_other_types`)
- [x] **AC-7.** Dado un negocio sin crédito, una venta de $48.000 crea solo la pata merchant $48.000 a `users.payout_wallet_url`, con `loan_id` null y sin repayment (→ `test_ac7_no_loan_full_to_merchant`)
- [x] **AC-8.** Dado CT, una venta de $10.000 da abono $1.500, trust_cut $27,27, patas lender $1.472,73 / merchant $8.500, sin pata trust (bajo `TRUST_LEG_MIN`) y `trust_pending` $27,27 (→ `test_ac8_trust_pending_below_min`)
- [x] **AC-9.** Dado CT con `allocated_total` $2.198.000 y `trust_pending` $27,27, una venta de $48.000 da abono $2.000, trust_cut $36,37, lender $1.963,63, merchant $46.000, pata trust $63,64 (bajo el mínimo, pero sale porque `paid_off`), `trust_pending` $0, crédito `paid` con `paid_at` y `loan_paid` para los dos (→ `test_ac9_last_sale_pays_off`)
- [x] **AC-10.** Dada una cuenta `revoked`, la venta queda `held`, sus patas `held`, el crédito asigna igual y `ledger_in` suma (→ `test_ac10_revoked_account_holds`)
- [x] **AC-11.** Dado un negocio sin payout wallet y sin crédito, la pata merchant queda `held` con `receiver_wallet_url` null (→ `test_ac11_no_wallet_holds_merchant_leg`)
- [x] **AC-12.** Con `NOTIFY_SALE_SPLIT=true` el negocio recibe un `sale_split`; con `false`, ninguno (→ `test_ac12_sale_split_notification`)
- [x] **AC-13.** Dado CT y hoy (16 oct) ventas de $48.000, $26.000, $12.500 y $85.000 más una de ayer, `GET /sales/today` da `summary` $171.500 / 4 / merchant $145.775 / abonado $25.725 (abonos $7.200, $3.900, $1.875 y $12.750, como COPY 13c), `rule` 8500/1500, `wallet_today` $145.775 y 4 filas desc; con `since = next_since` y una venta nueva, `rows` trae solo esa y `sales_count` 5 (→ `test_ac13_sales_today`)
- [x] **AC-14.** `GET /sales/{sale_id}` de una venta ajena → 403; de una propia → las 3 patas con estado, intento y URLs (→ `test_ac14_sale_detail_owner`)

## Fuera de alcance
- Reversos y contracargos (`qr_payment.reversed` se guarda y se ignora) · liberar patas `held` (16-admin) · Puntored real.
