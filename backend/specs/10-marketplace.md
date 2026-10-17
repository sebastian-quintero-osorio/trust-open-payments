# 10 · Marketplace: solicitudes, ofertas y matches

Estado: ☑ hecho · Fase: 2 · Dueño: Claude · Carpeta: `app/modules/marketplace/`

## Objetivo
Que el negocio publique una solicitud (rango + % de sus ventas) y el prestamista una oferta (rango + % + cargo + plazo), y que Trust calcule al vuelo qué encaja con qué (`solicitud-credito`, `detalle-match`, "Ofertas para ti (3)").

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `POST /api/v1/loan-requests` | merchant | `{min_amount, max_amount, pct_bps}` | 201 `LoanRequest` | 422 `validation_error`, `asset_mismatch`, `pct_not_allowed`, `not_eligible`, `exceeds_cupo` · 409 `active_loan_exists`, `open_request_exists` |
| ★ | `GET /api/v1/loan-requests?scope=mine\|market&status=&in_my_range=` | mine: merchant · market: lender | — | lista de `LoanRequest` | 403 `forbidden` · 422 `validation_error` |
|  | `GET /api/v1/loan-requests/{request_id}` | dueño o lender (si `open`) | — | `LoanRequest` | 403 · 404 |
|  | `POST /api/v1/loan-requests/{request_id}/cancel` | merchant dueño | — | `LoanRequest` | 403 · 404 · 409 `invalid_state` |
| ★ | `POST /api/v1/loan-offers` | lender | `{min_amount, max_amount, pct_bps, fee_bps, term_months?}` | 201 `LoanOffer` | 422 `validation_error`, `asset_mismatch`, `pct_not_allowed`, `wallet_required` |
| ★ | `GET /api/v1/loan-offers?scope=mine\|market&status=&fit_min=&fit_max=&pct_bps=&fits_only=` | mine: lender · market: merchant | — | lista de `LoanOffer` + `count` | 403 · 422 `validation_error` |
|  | `GET /api/v1/loan-offers/{offer_id}` | dueño o merchant (si `open`) | — | `LoanOffer` | 403 · 404 |
|  | `POST /api/v1/loan-offers/{offer_id}/cancel` | lender dueño | — | `LoanOffer` | 403 · 404 · 409 `invalid_state` |
| ★ | `GET /api/v1/matches` | merchant, lender | — | `{"items": [Match]}` | — |

`LoanRequest` (`merchant` y `credit` salen en `market` y en el detalle; en `mine`, al crear y al cancelar van en `null`):
```json
{"id": "r1…", "status": "open", "pct_bps": 1500, "credit_assessment_id": "c1f0…", "created_at": "2026-10-01T15:00:00Z",
 "min_amount": {"value": "150000000", "asset_code": "COP", "asset_scale": 2},
 "max_amount": {"value": "300000000", "asset_code": "COP", "asset_scale": 2},
 "merchant": {"id": "8b2e…", "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"},
 "credit": {"score": 845, "grade": "A", "cupo_total": {"value": "600000000", "asset_code": "COP", "asset_scale": 2},
            "cupo_disponible": {"value": "600000000", "asset_code": "COP", "asset_scale": 2}}}
```
`LoanOffer` en `market` (con `proposal` cuando aplica R8; `null` si no encaja). La lista trae `"count": 3`:
```json
{"id": "o1…", "status": "open", "lender": {"id": "5c7a…", "public_name": "Camilo P."},
 "min_amount": {"value": "100000000", "asset_code": "COP", "asset_scale": 2},
 "max_amount": {"value": "250000000", "asset_code": "COP", "asset_scale": 2},
 "pct_bps": 1500, "fee_bps": 500, "term_months": 3, "created_at": "2026-10-01T14:00:00Z",
 "proposal": {"amount": {"value": "200000000", "asset_code": "COP", "asset_scale": 2},
              "overlap_min": {"value": "150000000", "asset_code": "COP", "asset_scale": 2},
              "overlap_max": {"value": "250000000", "asset_code": "COP", "asset_scale": 2}, "pct_bps": 1500}}
```
`Match` = `{"request": {id, merchant, min_amount, max_amount, pct_bps, cupo_total}, "offer": {id, lender, min_amount, max_amount, pct_bps, fee_bps, term_months}, "proposal": {...como arriba}, "received_at": "2026-10-01T14:00:00Z"}`.

## Reglas
- **R1.** Montos: Money en el asset de mercado (si no, 422 `asset_mismatch`); `value` múltiplo de `AMOUNT_STEP`, `min ≥ MIN_LOAN_AMOUNT` y `min ≤ max` (si no, 422 `validation_error`). `pct_bps` ∈ `PCT_OPTIONS_BPS` (si no, 422 `pct_not_allowed`).
- **R2.** Crear solicitud, en este orden tras R1: crédito no terminado del negocio → 409 `active_loan_exists`; solicitud `open` → 409 `open_request_exists` (también si salta el índice único parcial); `get_credit_profile` no elegible → 422 `not_eligible`; `max > cupo_disponible` → 422 `exceeds_cupo`. Guarda `credit_assessment_id` del perfil usado.
- **R3.** Crear oferta: R1; `fee_bps` entre 0 y 10000 y `term_months` entre 1 y 24 (default `DEFAULT_TERM_MONTHS`) o 422 `validation_error`; el prestamista debe tener `payout_wallet_url` (422 `wallet_required`). Su wallet puede ser de otro asset.
- **R4.** `scope` es obligatorio (si falta, 422 `validation_error`). `scope=mine` lista las del usuario (cualquier estado, `?status=` filtra); `scope=market` solo las `open` de otros (un `?status=` distinto de `open` deja la lista vacía). Rol que no corresponde al scope → 403. Listas con `limit`/`offset` de `00-core`, más recientes primero.
- **R5.** `GET /loan-requests?scope=market`: cada ítem lleva `merchant` y `credit` (de `get_credit_profile` de ese negocio). `in_my_range=true` deja solo las que se cruzan con `[lender_range_min, lender_range_max]`; si el prestamista no tiene rango, se ignora.
- **R6.** Cancelar: solo el dueño (403); `open → cancelled` (si no, 409 `invalid_state`). `record_event` en la misma transacción.
- **R7.** `GET /loan-offers?scope=market`: `fit_min` y `fit_max` son enteros en unidades mínimas (centavos) del asset de mercado y van con `pct_bps`, los tres juntos o ninguno (422 `validation_error`, en cualquier scope; `mine` no los usa). Definen una solicitud hipotética; si no vienen, se usa la solicitud `open` del negocio; y si tampoco tiene, la solicitud virtual de 13 R4 `{min_amount: MIN_LOAN_AMOUNT, max_amount: cupo_disponible, pct_bps: max(PCT_OPTIONS_BPS)}`.
- **R8.** Si hay solicitud (R7), cada oferta lleva `proposal = fits(solicitud, oferta, cupo_disponible del negocio, AMOUNT_STEP)` y la respuesta trae `count` = ofertas con `proposal` no nula (todas, no solo la página). `fits_only=true` deja solo esas. No elegible o `cupo_disponible < MIN_LOAN_AMOUNT`: ninguna encaja (`count` 0). En `scope=mine`, `proposal` es null y `count` = total de ofertas que cumplen el filtro `status` (todas las páginas).
- **R9.** `GET /matches` (sin paginar, máx. 200, por `received_at` desc; admin → 403). El detalle de una oferta (`GET /loan-offers/{offer_id}`) lleva `proposal: null`. Merchant: su solicitud `open` contra cada oferta `open`, con su `cupo_disponible`. Lender: cada solicitud `open` contra cada oferta `open` suya, con el `cupo_disponible` de ese negocio. Solo los pares con `fits()` no nulo; `received_at = offer.created_at`.

## Datos
- Lee: `users`, `loans`, `credit_assessments` · Escribe: `loan_requests`, `loan_offers`, `events` (definidas en `01-data-model.md`).

## Depende de
- `05-policies` (`fits`). `09-scoring` (`get_credit_profile`). `04-users` (auth, `MerchantRef`, `LenderRef`, `lender_range_*`).

## Criterios de aceptación
Negocio elegible con `cupo_disponible` $6.000.000; cada oferta de un prestamista distinto; los montos viajan como Money de mercado (en tests, `cop(x)`: $1.500.000 → `{"value": "150000000", "asset_code": "COP", "asset_scale": 2}`).
- [x] **AC-1.** Dado el negocio, cuando publica mínimo $1.500.000, máximo $3.000.000 y `pct_bps` 1500, entonces 201 `open` con `credit_assessment_id` del perfil de hoy (→ `test_ac1_create_request`).
- [x] **AC-2.** Dado el negocio, cuando pide máximo $7.000.000, entonces 422 `exceeds_cupo`; dado un negocio no elegible, entonces 422 `not_eligible` (→ `test_ac2_request_cupo_and_eligibility`).
- [x] **AC-3.** Cuando `pct_bps` 1200 → 422 `pct_not_allowed`; mínimo $1.502.000 (no es múltiplo de `AMOUNT_STEP`) o mínimo > máximo → 422 `validation_error`; asset `EUR` → 422 `asset_mismatch` (→ `test_ac3_request_validation`).
- [x] **AC-4.** Dada una solicitud `open`, cuando publica otra → 409 `open_request_exists`; dado un crédito `pending_disbursement` → 409 `active_loan_exists` (→ `test_ac4_request_conflicts`).
- [x] **AC-5.** Dado un prestamista sin wallet, cuando publica oferta → 422 `wallet_required`; con wallet y sin `term_months` → 201 con `term_months` 3 (→ `test_ac5_create_offer`).
- [x] **AC-6.** Dado un prestamista con rango $1.000.000–$2.500.000 y solicitudes de dos negocios distintos (cupo ≥ $4.000.000), $1.500.000–$3.000.000 y $3.000.000–$4.000.000, cuando pide `scope=market&in_my_range=true`, entonces solo sale la primera, con `merchant` y `credit` (score, grado, cupos) (→ `test_ac6_requests_market_in_my_range`).
- [x] **AC-7.** Dada la solicitud $1.500.000–$3.000.000 @1500 y ofertas A $1.000.000–$2.500.000 @1500, B $3.500.000–$4.000.000 @1500, C $1.000.000–$2.000.000 @2000, D $2.000.000–$3.000.000 @1000 y E $500.000–$1.500.000 @1500, cuando el negocio pide `scope=market`, entonces `count` 3 y propuestas A $2.000.000, D $2.500.000, E $1.500.000, B y C `null`; lo mismo sin solicitud y con `fit_min=150000000&fit_max=300000000&pct_bps=1500`; con `fits_only=true` salen 3 ítems (→ `test_ac7_offers_market_fit_count`).
- [x] **AC-8.** Con los datos de AC-7, cuando el negocio pide `GET /matches`, entonces 3 ítems con `received_at` = `created_at` de cada oferta; cuando el dueño de A lo pide, entonces 1 ítem con la solicitud y `proposal.amount` $2.000.000 (→ `test_ac8_matches_both_roles`).
- [x] **AC-9.** Dada una solicitud `open`, cuando otro negocio la cancela → 403; cuando el dueño la cancela → `cancelled`; si la cancela otra vez → 409 `invalid_state` (→ `test_ac9_cancel_request`).
- [x] **AC-10.** Dado un negocio, `GET /loan-requests?scope=market` → 403; dado un prestamista, `GET /loan-offers?scope=market` → 403; dado un negocio con `fit_min` sin `fit_max` → 422 `validation_error` (→ `test_ac10_scope_roles_and_fit_params`).

## Fuera de alcance
- Notificar matches nuevos, editar solicitudes u ofertas y ofertas en assets distintos al de mercado.
