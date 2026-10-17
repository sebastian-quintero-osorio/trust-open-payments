# 13 · Dashboards: comercio y prestamista

Estado: ☐ pendiente · Fase: 3 · Dueño: — · Carpeta: `app/modules/dashboard/`

## Objetivo
Una sola llamada por pantalla de inicio: `cupo-dashboard` del comercio (COPY 13a) y `portafolio-prestamista` (COPY 13d). Solo lectura, sin llamar a Go.

Escala de montos: unidades mínimas del asset de mercado (USD, escala 2) = cifras de COPY.md ÷ 10 (cupo $6.000.000 → `600000`, abonos de hoy $186.400 → `18640`).

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `GET /api/v1/dashboard/merchant` | merchant | — | Abajo | 403 `forbidden` |
| ★ | `GET /api/v1/dashboard/lender` | lender | — | Abajo | 403 `forbidden` |

`GET /dashboard/merchant` (COPY 13a):
```json
{"greeting": {"first_name": "Marta", "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"},
 "unread_notifications": 0,
 "credit": {"cupo_total": {"value": "600000", "asset_code": "USD", "asset_scale": 2},
            "cupo_disponible": {"value": "400000", "asset_code": "USD", "asset_scale": 2},
            "score": 780, "grade": "A", "eligible": true, "unlocks_at": null},
 "active_loan": {"id": "…", "status": "active", "principal": {"value": "200000", "asset_code": "USD", "asset_scale": 2},
                 "pct_bps": 1500, "lender": {"id": "…", "public_name": "Camilo P."},
                 "allocated": {"value": "64000", "asset_code": "USD", "asset_scale": 2}, "pct_paid_bps": 3200,
                 "since": "2026-10-02T15:00:00Z", "is_late": false},
 "today": {"sales_total": {"value": "61200", "asset_code": "USD", "asset_scale": 2}, "sales_count": 14,
           "merchant_total": {"value": "52020", "asset_code": "USD", "asset_scale": 2},
           "abonado_total": {"value": "9180", "asset_code": "USD", "asset_scale": 2}},
 "offers_fit_count": 3,
 "collection_account": {"qr_payload": "https://pay.trustservices.com.co/qr/2c4e6a80-…", "status": "active"},
 "next_action": "view_loan"}
```

`GET /dashboard/lender` (COPY 13d):
```json
{"greeting": {"first_name": "Camilo", "public_name": "Camilo P."},
 "unread_notifications": 2, "active_loans_count": 3,
 "totals": [{"asset_code": "USD", "asset_scale": 2,
             "total_prestado": {"value": "550000", "asset_code": "USD", "asset_scale": 2},
             "abonos_hoy": {"gross": {"value": "18640", "asset_code": "USD", "asset_scale": 2},
                            "trust_cut": {"value": "339", "asset_code": "USD", "asset_scale": 2},
                            "net": {"value": "18301", "asset_code": "USD", "asset_scale": 2}},
             "recibido_hoy": {"value": "17680", "asset_code": "USD", "asset_scale": 2}}],
 "mi_rango": {"min": {"value": "100000", "asset_code": "USD", "asset_scale": 2}, "max": {"value": "250000", "asset_code": "USD", "asset_scale": 2}},
 "abonos_por_hora": [{"hour": "08:00", "amount": {"value": "3810", "asset_code": "USD", "asset_scale": 2}},
                     {"hour": "09:00", "amount": {"value": "7150", "asset_code": "USD", "asset_scale": 2}},
                     {"hour": "10:00", "amount": {"value": "7680", "asset_code": "USD", "asset_scale": 2}}],
 "loans": [{"id": "…", "merchant": {"id": "…", "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"},
            "principal": {"value": "200000", "asset_code": "USD", "asset_scale": 2}, "pct_bps": 1500, "pct_paid_bps": 3200,
            "today_gross": {"value": "9180", "asset_code": "USD", "asset_scale": 2}, "status": "active", "is_late": false}],
 "last_repayments": [{"id": "…", "paid_at": "2026-10-16T15:42:00Z", "merchant": {"id": "…", "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"},
                      "gross": {"value": "720", "asset_code": "USD", "asset_scale": 2}, "trust_cut": {"value": "13", "asset_code": "USD", "asset_scale": 2},
                      "net": {"value": "707", "asset_code": "USD", "asset_scale": 2}}]}
```

## Reglas
- **R1. `credit`** sale de `get_credit_profile(db, merchant_id)`: `cupo_total`, `cupo_disponible`, `score`, `grade`, `eligible`, `unlocks_at`.
- **R2. `active_loan`** = el crédito no terminado del negocio (cualquiera de los 4 estados no terminales de 01) o `null`. `allocated = allocated_total`; `pct_paid_bps = allocated_total × 10000 // total_repayable` (mismo nombre y fórmula que 11 R13); `since = activated_at` (null si aún no está `active`); `pct_bps` vigente.
- **R3. `today`** = `sales.service.today_summary(db, merchant_id, today_local())`: la misma cifra que `GET /sales/today` (abonado en bruto, 07 R8).
- **R4. `offers_fit_count`.** 0 si `eligible` es false o `cupo_disponible < MIN_LOAN_AMOUNT`. Si no, cuenta las `loan_offers` `open` con `fits(req, offer, cupo_disponible, AMOUNT_STEP) is not None`, donde `req` es la solicitud `open` del negocio o, si no tiene, una virtual `{min_amount: MIN_LOAN_AMOUNT, max_amount: cupo_disponible, pct_bps: max(PCT_OPTIONS_BPS)}`. Se cuenta aunque tenga un crédito no terminado (COPY muestra "Ofertas para ti (3)" junto al crédito activo).
- **R5. `collection_account`** = la cuenta `active` del negocio; si no hay, la más reciente; si no, `null`. Solo `{qr_payload, status}`.
- **R6. `next_action`**, la primera que aplique: `link_wallet` (sin `payout_wallet_url`) → `activate_qr` (sin cuenta `active`) → `accept_loan` (crédito `pending_acceptance`) → `wait_disbursement` (`pending_disbursement` o `disbursing`) → `view_loan` (`active`) → `wait_unlock` (no elegible) → `view_offers` (solicitud `open` y `offers_fit_count > 0`) → `request_credit`.
- **R7. `unread_notifications`** = notificaciones del usuario con `read_at` null (el mismo `unread_count` de 14).
- **R8. Prestamista · créditos.** `active_loans_count` = sus créditos `active`. `loans` = sus créditos no terminados, `active` primero y luego `created_at` desc. `pct_paid_bps` como R2. `today_gross` = Σ `abono_amount` de los repayments del crédito con venta de hoy (local).
- **R9. `totals`**, agrupados por (`asset_code`, `asset_scale`) del crédito: `total_prestado` = Σ `principal` de créditos `active` + `disbursing`; `abonos_hoy` = `{gross: Σ abono_amount, trust_cut: Σ trust_cut_amount, net: Σ lender_net_amount}` de sus repayments con venta de hoy; `recibido_hoy` = Σ `sent_total` de las patas `lender` de sus créditos con `completed_at` hoy (en el asset del crédito, no del receptor). La app muestra el bruto como COPY ("+$186.400") y el desglose debajo. Siempre incluye la entrada del asset de mercado (en 0 si no hay datos); otros assets solo si tienen datos.
- **R10. `abonos_por_hora`**: Σ `abono_amount` (bruto) por hora local de `paid_at` de hoy, desde la primera hasta la última hora con abonos, con 0 en las horas vacías; `hour` = `"HH:00"`. MVP: solo el asset de mercado. Sin abonos hoy → `[]`.
- **R11. `last_repayments`**: los 5 repayments más recientes del prestamista por `paid_at` (de cualquier día), con `gross`, `trust_cut`, `net`.
- **R12. `mi_rango`** = `lender_range_min/max` como Money del asset de mercado; `null` si alguno es null.
- **R13.** "Hoy" siempre es `today_local()` / `local_day_bounds` de 00-core. Sin N+1: agregados con `GROUP BY`.

## Datos
- Lee: `users`, `loans`, `loan_requests`, `loan_offers`, `sales`, `repayments`, `payouts`, `collection_accounts`, `notifications`, `credit_assessments` (vía 09) · Escribe: nada.

## Depende de
- `09-scoring` (`get_credit_profile`) · `05-policies` (`fits`) · `07-sales` (`today_summary`) · `14-notifications` (conteo de no leídas) · `00-core` (MerchantRef, LenderRef, `clock`).

## Criterios de aceptación
- [ ] **AC-1.** Dado un `credit_assessment` de hoy (cupo_total 600.000, score 780, A, elegible), un crédito `active` de 200.000 con `fee_bps` 0 (total 200.000), `allocated_total` 64.000, `activated_at` 2 oct, prestamista Camilo P. y cuenta `active`, cuando Marta pide su dashboard, entonces `cupo_disponible` 400.000, `pct_paid_bps` 3200, `lender.public_name` "Camilo P.", `initials` "TL" y `next_action` `view_loan` (→ `test_ac1_merchant_dashboard_copy_numbers`)
- [ ] **AC-2.** Dadas hoy ventas de 4.800, 2.600, 1.250 y 8.500 al 15 %, `today` da 17.150 / 4 / 14.578 / 2.572, igual que `summary` de `GET /sales/today` (→ `test_ac2_today_matches_sales_feed`)
- [ ] **AC-3.** Dadas ofertas `open` A 100.000–250.000 al 15 %, B 350.000–500.000 al 15 % y C 100.000–200.000 al 20 % y `cupo_disponible` 400.000: con la solicitud 150.000–300.000 al 15 % el conteo es 1; sin solicitud es 3; si no es elegible, 0 (→ `test_ac3_offers_fit_count`)
- [ ] **AC-4.** Dado un negocio sin payout wallet, sin cuenta ni crédito, entonces `active_loan` null, `collection_account` null, `today` en 0 con `sales_count` 0 y `next_action` `link_wallet` (→ `test_ac4_merchant_empty_state`)
- [ ] **AC-5.** Dado un prestamista con 2 créditos `active` (200.000 y 150.000), 1 `disbursing` (200.000) y 1 `pending_disbursement` (100.000), entonces `active_loans_count` 2, `total_prestado` 550.000 y `loans` con 4 items, los `active` primero (→ `test_ac5_lender_totals`)
- [ ] **AC-6.** Dados abonos de hoy a las 08:15 (bruto 720, trust 13, neto 707) y 10:05 (bruto 390) y uno de ayer, entonces `abonos_hoy.gross` 1.110, `net` = Σ lender_net de los dos, `abonos_por_hora` [08:00 → 720, 09:00 → 0, 10:00 → 390], `today_gross` por crédito y `last_repayments` con 3 items, el de 10:05 primero (→ `test_ac6_lender_hourly_and_last`)
- [ ] **AC-7.** Dada una pata lender `completed` hoy con `sent_total` 707 y otra `completed` ayer, `recibido_hoy` es 707 (→ `test_ac7_received_today`)
- [ ] **AC-8.** Un merchant en `/dashboard/lender` → 403, y un lender en `/dashboard/merchant` → 403 (→ `test_ac8_roles`)

## Fuera de alcance
- Dashboard de admin (16) · gráficos de días anteriores · caché.
