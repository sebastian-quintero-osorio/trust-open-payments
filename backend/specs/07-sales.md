# 07 · Ventas: webhook de Puntored y división

Estado: ☐ pendiente · Fase: 1 · Dueño: — · Carpeta: `app/modules/sales/`

## Objetivo
Recibir cada pago Bre-B (webhook de Puntored, simulado por op-service), dividirlo en una sola transacción (venta → abono → patas) y mostrarle al comercio sus ventas del día con su división (COPY 13c `division-en-vivo`).

Escala de montos: unidades mínimas del asset de mercado (USD, escala 2) = cifras de COPY.md ÷ 10 (venta $48.000 → `4800`).

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `POST /api/v1/webhooks/puntored/payments` | — (HMAC, sin JWT) | Header `X-Puntored-Signature: t=<unix>,v1=<hex>` + body `PuntoredPaymentEvent` (OpenAPI) | 200 `{"status": "processed" \| "duplicate" \| "unmatched" \| "ignored"}` | 401 `invalid_signature`, 401 `signature_expired`, 400 `bad_request` (JSON ilegible o sin `event_id`/`type`) |
| ★ | `GET /api/v1/sales/today?since=` | merchant | `since` opcional (ISO, el `next_since` anterior) | Feed del día (abajo) | 422 `validation_error` |
| | `GET /api/v1/sales?from=&to=` | merchant | Fechas locales `YYYY-MM-DD` inclusivas + paginación | `{"items": [Fila], "next_offset"}` | 422 `validation_error` |
| | `GET /api/v1/sales/{id}` | merchant (dueño), admin | — | `{"sale", "repayment" \| null, "payouts": [...]}` | 403 `forbidden`, 404 `not_found` |

`Fila` = `{sale_id, paid_at, amount, merchant_amount, abono_amount, payout_status}` (Money en los montos).

`GET /sales/today` (hoy 16 oct, crédito al 15 %):
```json
{"date": "2026-10-16", "live": true,
 "summary": {"sales_total": {"value": "61200", "asset_code": "USD", "asset_scale": 2}, "sales_count": 14,
             "merchant_total": {"value": "52020", "asset_code": "USD", "asset_scale": 2},
             "abonado_total": {"value": "9180", "asset_code": "USD", "asset_scale": 2}},
 "rule": {"merchant_bps": 8500, "lender_bps": 1500},
 "wallet_today": {"value": "52020", "asset_code": "USD", "asset_scale": 2},
 "rows": [{"sale_id": "…", "paid_at": "2026-10-16T15:42:00Z",
           "amount": {"value": "4800", "asset_code": "USD", "asset_scale": 2},
           "merchant_amount": {"value": "4080", "asset_code": "USD", "asset_scale": 2},
           "abono_amount": {"value": "720", "asset_code": "USD", "asset_scale": 2}, "payout_status": "in_progress"}],
 "next_since": "2026-10-16T15:42:01.204Z"}
```

`GET /sales/{id}`: `sale {id, amount, paid_at, bre_b_reference, payer_bank, incoming_payment_url, source, status}` · `repayment {id, loan_id, abono, trust_cut, lender_net, pct_applied_bps} | null` · `payouts [{id, leg, amount, sent_total, status, attempt, receive (Money o null), incoming_payment_url, outgoing_payment_url, completed_at, failure_code}]`.

## Reglas
- **R1. Firma.** `raw = await request.body()` antes de parsear. El header son pares `k=v` separados por coma; exige `t` entero y al menos un `v1`. Válida si para algún secreto de `PUNTORED_WEBHOOK_SECRETS` y algún `v1`: `hmac.compare_digest(hmac_sha256(secreto, f"{t}.".encode() + raw).hexdigest(), v1)`. Sin header, mal formado o sin coincidencia → 401 `invalid_signature`; firma válida con `|clock.now() − t| > 300 s` → 401 `signature_expired`. Con 401 no se escribe nada.
- **R2. Router.** Firma válida → `json.loads(raw)` → `process_puntored_event(db, event, "puntored")` → commit → 200 `{"status": result.status}`. Excepción inesperada → rollback y 500 (Go reintenta; el dedup lo hace seguro). La ruta no usa JWT ni depende de CORS.
- **R3. `process_puntored_event(db, event, source) -> WebhookResult(status, sale_id | None)`.** No hace commit (lo hace quien llama: router o worker) y no llama a Go. Pasos en orden:
  1. `INSERT webhook_events (event_id, source, type, payload, received_at) ON CONFLICT (event_id) DO NOTHING` (el `status` final se escribe al terminar). Si no insertó → `duplicate` y no toca nada más.
  2. `type != "qr_payment.received"` → `ignored`.
  3. Valida `data` (si falta un campo o el monto no es positivo → `invalid_payload`) y luego busca la cuenta por `op_collection_account_id = data.collection_account_id`, con `FOR UPDATE`. Si `data` es inválida, la cuenta no existe (`unknown_account`), `wallet_address != data.collection_wallet_address` (`wallet_mismatch`) o el asset del monto no es el de la cuenta (`asset_mismatch`) → `unmatched` con ese `error` + `record_event`. Nunca 4xx.
  4. Bloquea (`FOR UPDATE`, después de la cuenta) el crédito no terminado del negocio. Solo abona si `status == active`.
  5. Ya existe venta con (`collection_account_id`, `bre_b_reference`) → `duplicate`; el webhook_event queda `ignored` con `error = duplicate_reference`.
  6. Inserta `sales`: `puntored_event_id`, `bre_b_reference`, `amount`, `payer_bank`, `incoming_payment_url`, `paid_at = occurred_at`, `source = live`, `status = processed` (o `held` si la cuenta no está `active`).
  7. `s = split_sale(amount, SplitLoanState(pct_bps, total_repayable, allocated_total, fee_amount, trust_share_bps) si hay crédito active, si no None, policy=TRUST_CUT_POLICY)`.
  8. Si `s.abono > 0`: inserta `repayments` (`pct_applied_bps = loan.pct_bps`, `allocated_before = loan.allocated_total`, `allocated_after = s.allocated_after`) y en el crédito: `allocated_total = s.allocated_after`, `trust_earned += s.trust_cut`, `trust_pending += s.trust_cut`.
  9. Pata Trust: `t = trust_pending` si `trust_pending > 0` y (`trust_pending ≥ TRUST_LEG_MIN` o `s.paid_off`), si no `t = 0`; `trust_pending −= t`.
  10. Inserta las patas con monto > 0 (R4) y un `record_event` por pata.
  11. `collection_accounts.ledger_in += amount`.
  12. Si `s.paid_off`: crédito `paid`, `paid_at = now`, `record_event`, `notify loan_paid` al negocio y al prestamista.
  13. webhook_event `processed` con `sale_id` y `processed_at`. Si `NOTIFY_SALE_SPLIT`: `notify sale_split` al negocio.
- **R4. Patas** (receptor congelado, asset de la venta, `attempt = 1`, `current_debit_amount = amount`, `status = pending`, o `held` si la venta es `held`): `lender` = `s.lender_net` → `loan.lender_wallet_url` · `merchant` = `s.merchant_amount` → `loan.merchant_payout_wallet_url` si hay crédito active, si no `users.payout_wallet_url` · `trust` = `t` → `TRUST_REVENUE_WALLET_ADDRESS`. `loan_id` = crédito active (null en `merchant` sin crédito). Negocio sin payout wallet → pata `merchant` `held` con `receiver_wallet_url = null`. La pata Trust puede sumar `trust_pending` de ventas anteriores; `lender + merchant + trust_cut = amount` siempre.
- **R5. Venta `held`** (cuenta revocada): se divide y se asigna al crédito igual (la plata ya está en recaudo); solo las patas quedan `held`. Liberarlas es de 16-admin.
- **R6. Descripción congelada** (≤ 200, con `{hora}` = `clock.format_hora(paid_at)`, formato `10:42 a. m.`, nunca `now()`): lender `Abono venta {hora} · {negocio}` · merchant `Venta {hora} · {negocio}` · trust `Parte Trust venta {hora} · {negocio}`.
- **R7. Estado de entrega agregado** (`payout_status` de una venta; 12 y 13 usan el mismo vocabulario): `failed` si alguna pata `failed_final`; si no `held` si alguna `held`; si no `in_progress` si alguna `pending`/`processing`; si no `completed` (también sin patas). Las patas `skipped` se ignoran.
- **R8. Cifras del comercio.** `abono_amount` es el abono **bruto** (incluye la parte Trust: "15 % a tu crédito"). `merchant_amount = amount − abono` (abono del repayment, 0 si no hay; así cuentan también las ventas `seed`). `rule` = `{merchant_bps: 10000 − pct_bps, lender_bps: pct_bps}` del crédito `active`, o `{10000, 0}`.
- **R9. `/sales/today`.** Día `today_local()` con `local_day_bounds`, ventas del negocio por `paid_at` (todas las fuentes). `summary` sobre todo el día; `rows` (orden `paid_at` desc) solo con `created_at > since` si viene `since`. `next_since` = máximo `created_at` de las ventas del día (o el `since` recibido; null si no hay ventas). `wallet_today = merchant_total` (la app lo rotula "En tu wallet Puntored hoy"). `live` siempre `true`.
- **R10. `today_summary(db, merchant_id, day) -> TodaySummary(sales_total, sales_count, merchant_total, abonado_total)`** en `sales/service.py` calcula `summary`; 13-dashboard usa la misma función.
- **R11.** `/sales` y `/sales/{id}`: el negocio solo ve sus ventas (ajena → 403); en el detalle ve las tres patas.

## Datos
- Lee: `users`, `collection_accounts`, `loans` · Escribe: `webhook_events`, `sales`, `repayments`, `payouts`, `loans` (`allocated_total`, `trust_earned`, `trust_pending`, `status`, `paid_at`), `collection_accounts` (`ledger_in`), `events`, `notifications`.

## Depende de
- `05-policies` (`split_sale`) · `06-recaudo` (cuentas de recaudo) · `11-loans` (créditos `active`; el paso a `paid` lo hace esta spec, ver máquina de estados en 01) · `14-notifications` (`notify` y textos) · `00-core` (`clock`, `record_event`, `money`).

## Criterios de aceptación
Crédito tipo (CT): principal 200.000, `fee_bps` 1000 (cargo 20.000, total 220.000), `trust_share_bps` 2000, `pct_bps` 1500, `allocated_total` 0, `trust_pending` 0, `active`. Con CT, `T(r) = floor(r / 55)`. Config por defecto (`TRUST_LEG_MIN` 10, `TRUST_CUT_POLICY` prorated).
- [ ] **AC-1.** Dado CT, cuando llega un webhook firmado con una venta de 4.800, entonces 200 `processed`, 1 venta, repayment (abono 720, trust_cut 13, lender_net 707, pct 1500, allocated 0 → 720), patas `pending` lender 707 / merchant 4.080 / trust 13, `ledger_in` 4.800, `trust_earned` 13, `trust_pending` 0 (→ `test_ac1_webhook_splits_sale`)
- [ ] **AC-2.** Dado un header ausente o firmado con un secreto ajeno → 401 `invalid_signature`; firmado bien con `t = now − 301` → 401 `signature_expired`; en ambos casos `webhook_events` vacío (→ `test_ac2_rejects_bad_signature`)
- [ ] **AC-3.** Dado `PUNTORED_WEBHOOK_SECRETS="viejo,nuevo"` y un body con espacios y claves en otro orden, cuando se firma con `nuevo` sobre esos bytes exactos, entonces 200 (→ `test_ac3_secret_rotation_raw_body`)
- [ ] **AC-4.** Dado el mismo `event_id` dos veces, la segunda da `duplicate`; con otro `event_id` y el mismo `bre_b_reference` también `duplicate` (webhook_event `ignored`); queda 1 venta con 3 patas (→ `test_ac4_dedup`)
- [ ] **AC-5.** Dada una cuenta desconocida, una wallet que no cuadra o un monto en EUR → 200 `unmatched`, webhook_event `unmatched` con su `error`, sin venta (→ `test_ac5_unmatched`)
- [ ] **AC-6.** Dado `type = qr_payment.reversed` → 200 `ignored`, sin venta (→ `test_ac6_ignores_other_types`)
- [ ] **AC-7.** Dado un negocio sin crédito, una venta de 4.800 crea solo la pata merchant 4.800 a `users.payout_wallet_url`, con `loan_id` null y sin repayment (→ `test_ac7_no_loan_full_to_merchant`)
- [ ] **AC-8.** Dado CT, una venta de 1.000 da abono 150, trust_cut 2, patas lender 148 / merchant 850, sin pata trust y `trust_pending` 2 (→ `test_ac8_trust_pending_below_min`)
- [ ] **AC-9.** Dado CT con `allocated_total` 219.800 y `trust_pending` 2, una venta de 4.800 da abono 200, trust_cut 4, lender 196, merchant 4.600, pata trust 6 (bajo el mínimo, pero sale porque `paid_off`), `trust_pending` 0, crédito `paid` con `paid_at` y `loan_paid` para los dos (→ `test_ac9_last_sale_pays_off`)
- [ ] **AC-10.** Dada una cuenta `revoked`, la venta queda `held`, sus patas `held`, el crédito asigna igual y `ledger_in` suma (→ `test_ac10_revoked_account_holds`)
- [ ] **AC-11.** Dado un negocio sin payout wallet y sin crédito, la pata merchant queda `held` con `receiver_wallet_url` null (→ `test_ac11_no_wallet_holds_merchant_leg`)
- [ ] **AC-12.** Con `NOTIFY_SALE_SPLIT=true` el negocio recibe un `sale_split`; con `false`, ninguno (→ `test_ac12_sale_split_notification`)
- [ ] **AC-13.** Dado CT y hoy (16 oct) ventas de 4.800, 2.600, 1.250 y 8.500 más una de ayer, `GET /sales/today` da `summary` 17.150 / 4 / merchant 14.578 / abonado 2.572, `rule` 8500/1500, `wallet_today` 14.578 y 4 filas desc; con `since = next_since` y una venta nueva, `rows` trae solo esa y `sales_count` 5 (→ `test_ac13_sales_today`)
- [ ] **AC-14.** `GET /sales/{id}` de una venta ajena → 403; de una propia → las 3 patas con estado, intento y URLs (→ `test_ac14_sale_detail_owner`)

## Fuera de alcance
- Reversos y contracargos (`qr_payment.reversed` se guarda y se ignora) · liberar patas `held` (16-admin) · Puntored real.
