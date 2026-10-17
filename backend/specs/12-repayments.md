# 12 · Abonos: lista y traza

Estado: ☐ pendiente · Fase: 3 · Dueño: — · Carpeta: `app/modules/repayments/`

## Objetivo
Mostrar cada abono (bruto, parte Trust, neto, recibido) y su traza venta → abono → transferencias Open Payments → crédito. "Cada abono es trazable hasta la venta que lo originó." (COPY 13d).

Escala de montos: unidades mínimas del asset de mercado (USD, escala 2) = cifras de COPY.md ÷ 10 (venta $48.000 → `4800`).

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `GET /api/v1/repayments?loan_id=&from=&to=` | lender, merchant, admin | Filtros opcionales (`from`/`to` fechas locales `YYYY-MM-DD`) + paginación | `{"items": [Abono], "next_offset"}` | 403 `forbidden` (crédito ajeno), 404 `not_found` (crédito), 422 `validation_error` |
| | `GET /api/v1/repayments/{id}` | lender o merchant del crédito, admin | — | Traza (abajo) | 403 `forbidden`, 404 `not_found` |

`Abono`:
```json
{"id": "…", "sale_id": "…", "loan_id": "…",
 "merchant": {"id": "…", "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"},
 "lender": {"id": "…", "public_name": "Camilo P."},
 "paid_at": "2026-10-16T15:42:00Z",
 "abono": {"value": "720", "asset_code": "USD", "asset_scale": 2},
 "trust_cut": {"value": "13", "asset_code": "USD", "asset_scale": 2},
 "lender_net": {"value": "707", "asset_code": "USD", "asset_scale": 2},
 "pct_applied_bps": 1500,
 "delivery": {"status": "completed", "received": {"value": "650", "asset_code": "EUR", "asset_scale": 2}}}
```

Traza:
```json
{"id": "…",
 "sale": {"id": "…", "amount": {"value": "4800", "asset_code": "USD", "asset_scale": 2}, "paid_at": "2026-10-16T15:42:00Z",
          "bre_b_reference": "BREB-SIM-000123", "incoming_payment_url": "https://ilp.interledger-test.dev/…/incoming-payments/…"},
 "repayment": {"abono": {"value": "720", "asset_code": "USD", "asset_scale": 2}, "trust_cut": {"value": "13", "asset_code": "USD", "asset_scale": 2},
               "lender_net": {"value": "707", "asset_code": "USD", "asset_scale": 2}, "pct_applied_bps": 1500,
               "allocated_before": {"value": "0", "asset_code": "USD", "asset_scale": 2}, "allocated_after": {"value": "720", "asset_code": "USD", "asset_scale": 2}},
 "payouts": [{"id": "…", "leg": "lender", "amount": {"value": "707", "asset_code": "USD", "asset_scale": 2},
              "sent_total": {"value": "707", "asset_code": "USD", "asset_scale": 2}, "status": "completed", "attempt": 1,
              "receive": {"value": "650", "asset_code": "EUR", "asset_scale": 2}, "incoming_payment_url": "…", "outgoing_payment_url": "…",
              "completed_at": "2026-10-16T15:42:09Z", "failure_code": null}],
 "loan": {"id": "…", "status": "active", "pct_bps": 1500,
          "merchant": {"id": "…", "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"}, "lender": {"id": "…", "public_name": "Camilo P."},
          "principal": {"value": "200000", "asset_code": "USD", "asset_scale": 2}, "total_repayable": {"value": "220000", "asset_code": "USD", "asset_scale": 2},
          "allocated_total": {"value": "720", "asset_code": "USD", "asset_scale": 2}, "delivered_total": {"value": "720", "asset_code": "USD", "asset_scale": 2}}}
```

## Reglas
- **R1. Visibilidad.** lender → `repayments.lender_id = yo`; merchant → `merchant_id = yo`; admin → todos. `loan_id` de un crédito del que no es parte → 403; inexistente → 404. `GET /repayments/{id}` ajeno → 403.
- **R2. Filtros y orden.** `from`/`to` inclusivos sobre `sales.paid_at`, con `local_day_bounds` (día de Bogotá). Orden `paid_at` desc y luego `id`. Paginación de 00-core.
- **R3. Montos.** `abono`, `trust_cut`, `lender_net` salen de `repayments` (asset del crédito); `paid_at` es el de la venta.
- **R4. `delivery`** sale solo de la pata `lender` de la misma venta: `pending`/`processing` → `in_progress`, `completed` → `completed`, `held` → `held`, `failed_final` → `failed`; sin pata lender (`lender_net` = 0) o `skipped` → `none` (mismo vocabulario que 07 R7). `received` = `receive_value/receive_asset_*` de esa pata (asset del receptor) si no es null; si no, `null`.
- **R5. Traza.** Orden venta → abono → patas → crédito. El prestamista ve las patas `lender` y `trust`; el negocio y el admin ven las tres. Nunca se expone `receiver_wallet_url`.
- **R6.** Solo lectura, sin llamar a Go. La lista sale de una consulta con joins (`repayments` ⋈ `sales` ⋈ `users` ⋈ pata `lender`), sin N+1.

## Datos
- Lee: `repayments`, `sales`, `payouts`, `loans`, `users` · Escribe: nada.

## Depende de
- `07-sales` (repayments, patas, vocabulario de entrega) · `08-worker` (`receive_*`, `sent_total`, URLs) · `00-core` (MerchantRef, LenderRef, paginación, `local_day_bounds`).

## Criterios de aceptación
Base: el crédito tipo (CT) y la venta del AC-1 de 07 (4.800 → abono 720, trust_cut 13, lender_net 707).
- [ ] **AC-1.** Dado el prestamista A con ese abono y el prestamista B con otro, cuando A lista, entonces ve 1 item: abono 720, trust_cut 13, lender_net 707, pct 1500, merchant `TL`, lender `Camilo P.`, delivery `in_progress`, received null (→ `test_ac1_lender_lists_own`)
- [ ] **AC-2.** Dada la pata lender `completed` con `receive` 650 EUR (scale 2), delivery es `completed` con ese Money; con la pata `held` es `held` y con `failed_final` es `failed` (→ `test_ac2_delivery_status`)
- [ ] **AC-3.** El negocio ve sus abonos; un prestamista con `loan_id` de un crédito ajeno → 403; `loan_id` inexistente → 404 (→ `test_ac3_visibility_and_loan_filter`)
- [ ] **AC-4.** Con `from=to=2026-10-16`, aparece el abono de una venta de `2026-10-17T03:00Z` (22:00 del 16 en Bogotá) y no el de una de `2026-10-17T05:00Z` (→ `test_ac4_local_date_filter`)
- [ ] **AC-5.** `GET /repayments/{id}` devuelve venta (4.800, `bre_b_reference`, `incoming_payment_url`) → abono (allocated 0 → 720) → patas con `attempt`, `sent_total` y URLs → crédito; el prestamista ve 2 patas (lender, trust) y el negocio 3 (→ `test_ac5_trace`)
- [ ] **AC-6.** Dados 3 abonos, `limit=2` da 2 items y `next_offset` 2; `offset=2` da 1 y `next_offset` null (→ `test_ac6_pagination`)

## Fuera de alcance
- Exportar (CSV/PDF) · totales por periodo (los da 13-dashboard) · reversos.
