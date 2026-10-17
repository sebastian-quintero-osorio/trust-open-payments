# 09 · Scoring: score, grado y cupo

Estado: ☑ hecho · Fase: 2 · Dueño: Claude · Carpeta: `app/modules/scoring/`

## Objetivo
Calcular y guardar el score y el cupo de cada negocio a partir de sus ventas por recaudo, y mostrarlos al negocio (`cupo-dashboard`) y, en versión resumida, al prestamista que evalúa su solicitud.

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `GET /api/v1/me/credit-profile` | merchant | — | `CreditProfileOut` | — |
| ★ | `GET /api/v1/merchants/{merchant_id}/credit-profile` | lender | — | `MerchantCreditView` | 403 `forbidden` · 404 `not_found` |

`CreditProfileOut`:
```json
{"assessment_id": "c1f0…", "policy_version": "v1", "computed_at": "2026-10-16T14:00:00Z",
 "score": 845, "grade": "A", "eligible": true, "unlocks_at": null, "active_months": 6,
 "cupo_total": {"value": "600000000", "asset_code": "COP", "asset_scale": 2},
 "cupo_usado": {"value": "200000000", "asset_code": "COP", "asset_scale": 2},
 "cupo_disponible": {"value": "400000000", "asset_code": "COP", "asset_scale": 2},
 "reasons": ["Buen volumen de ventas en los últimos 3 meses.", "Ventas constantes semana a semana.", "Sin historial de créditos: cuenta como neutro."]}
```
`MerchantCreditView` (sin cifras exactas de ventas):
```json
{"merchant": {"id": "8b2e…", "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"},
 "score": 845, "grade": "A", "eligible": true, "active_months": 6,
 "sales_band": {"min": {"value": "1000000000", "asset_code": "COP", "asset_scale": 2},
                "max": {"value": "2500000000", "asset_code": "COP", "asset_scale": 2}},
 "cupo_total": {"value": "600000000", "asset_code": "COP", "asset_scale": 2},
 "cupo_disponible": {"value": "400000000", "asset_code": "COP", "asset_scale": 2},
 "computed_at": "2026-10-16T14:00:00Z"}
```

## Reglas
- **R1.** `features.py`: `build_features(db, merchant_id, now) -> MerchantFeatures` llena cada campo exactamente como lo define `05-policies` R13 (`as_of = now`). Lee `sales` del negocio con `source` `live` y `seed` y cualquier `status`, agregando por mes, semana y día con `paid_at AT TIME ZONE TIMEZONE` en SQL. Solo cuentan ventas con `paid_at ≤ now`; los montos suman solo ventas del asset de mercado. `months_on_platform` cuenta meses completos como una edad, en hora local (del 20 jul al 16 oct son 2). `loans_paid` = créditos del negocio en `paid`; `pct_increases` = filas de `loan_pct_changes` con reason `missed_minimum` de sus créditos.
- **R2.** `get_credit_profile(db, merchant_id) -> CreditProfile(assessment, cupo_total, cupo_usado, cupo_disponible)`. Reutiliza el último `credit_assessments` del negocio si `computed_at` cae en el día local de hoy (`local_day_bounds(today_local())`), su `policy_version == SCORING_POLICY` **y** no hay ninguna venta del negocio con `created_at > computed_at` (así `/dev/seed` y las ventas nuevas invalidan la caché). Si no, `build_features` → `policies.scoring.assess` → inserta una fila nueva (`features` y `reasons` como jsonb, asset de mercado, `computed_at = now`) y hace commit: se llama antes de bloquear filas (11 R6), nunca con una transacción con `FOR UPDATE` abierta.
- **R3.** `cupo_usado` = `principal` del crédito no terminado del negocio (cualquier estado no terminal de `01-data-model`), 0 si no hay. `cupo_disponible = max(0, cupo_total − cupo_usado)`.
- **R4.** `GET /me/credit-profile` siempre responde 200 (un negocio sin ventas sale no elegible, cupo 0 y su razón). `active_months` sale de `assessment.features`.
- **R5.** `GET /merchants/{merchant_id}/credit-profile`: 404 si `id` no existe o no es `merchant`. 403 salvo que el negocio tenga una solicitud `open` o algún crédito (en cualquier estado) con este prestamista.
- **R6.** `sales_band` se arma con `avg_monthly_sales_3m` y los cortes fijos `SALES_BANDS = [0, 250000000, 500000000, 1000000000, 2500000000, 5000000000, 10000000000, 20000000000]` (centavos del asset de mercado: $0, $2.500.000, $5.000.000, $10.000.000, $25.000.000, $50.000.000, $100.000.000 y $200.000.000): `min` = mayor corte ≤ promedio; `max` = siguiente corte, o `null` en el último tramo. Nunca se devuelve el promedio exacto ni `features`.

## Datos
- Lee: `sales`, `loans`, `loan_pct_changes`, `users`, `loan_requests` · Escribe: `credit_assessments` (definidas en `01-data-model.md`).

## Depende de
- `05-policies` (`assess`, `MerchantFeatures`, R13).
- `04-users` (`current_user`, `require_role`, `MerchantRef`).
- `07-sales` / `03-platform-dev` (filas de `sales`; en tests se insertan directo).

## Criterios de aceptación
Reloj congelado el 16 oct 2026 10:00 (Bogotá).
- [x] **AC-1.** Dado un negocio con ventas sembradas de 12 ventas por mes en julio, agosto y septiembre ($15.000.000, $16.000.000 y $17.000.000 en total) y 5 ventas en junio, cuando `build_features`, entonces `active_months` 3, `avg_monthly_sales_3m` $16.000.000 y `first_sale_at` = la primera venta de junio (→ `test_ac1_features_from_sales`).
- [x] **AC-2.** Dado ese negocio, cuando se llama dos veces `get_credit_profile` el mismo día, entonces existe una sola fila en `credit_assessments` (`policy_version` `v1`) y las dos llamadas devuelven el mismo `assessment.id` (→ `test_ac2_assessment_cached_for_local_day`).
- [x] **AC-3.** Dado un assessment de hoy, cuando se inserta una venta nueva (o el reloj pasa al 17 oct local), entonces la siguiente llamada crea una fila nueva (→ `test_ac3_cache_invalidated_by_sale_or_day`).
- [x] **AC-4.** Dado un assessment de hoy con `cupo_total` $6.000.000 y un crédito `active` de $2.000.000, cuando `get_credit_profile`, entonces `cupo_usado` $2.000.000 y `cupo_disponible` $4.000.000; con el crédito en `pending_disbursement` igual; con el crédito `paid`, disponible $6.000.000 (→ `test_ac4_cupo_disponible`).
- [x] **AC-5.** Dado un negocio con un solo mes activo, cuando hace `GET /api/v1/me/credit-profile`, entonces 200 con `eligible` falso, `cupo_total` Money en 0, `unlocks_at` `2026-12-01T05:00:00Z` y `reasons` no vacío (→ `test_ac5_me_profile_not_eligible`).
- [x] **AC-6.** Dado un prestamista sin relación con el negocio, cuando pide `GET /api/v1/merchants/{merchant_id}/credit-profile`, entonces 403 `forbidden`; cuando el negocio publica una solicitud `open`, entonces 200 con `sales_band` $10.000.000–$25.000.000 (promedio $16.000.000) y sin `features` ni promedio exacto (→ `test_ac6_lender_view_requires_relation`).
- [x] **AC-7.** Dado un negocio que llama `GET /api/v1/merchants/{merchant_id}/credit-profile`, entonces 403; dado un prestamista con un `id` inexistente, entonces 404 (→ `test_ac7_lender_view_roles_and_404`).

## Fuera de alcance
- Endpoint admin para recalcular, otras versiones de política y consentimiento formal para compartir el perfil (Ley 1581).
