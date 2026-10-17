# 05 · Políticas puras (split, términos, matching, recálculo, scoring)

Estado: ☑ hecho · Fase: 1 · Dueño: Claude · Carpeta: `app/policies/`

## Objetivo
Las reglas de dinero y de crédito como funciones puras: sin base de datos, sin red, sin `clock.now()`. Las usan 07-sales, 09-scoring, 10-marketplace y 11-loans con las firmas exactas de `00-core.md` ("Contratos entre módulos"). Todo en enteros (unidades mínimas y bps), con `bps_of` de `core/money.py`. `cfg` es el `Settings` de `core.config.get_settings()` (campos en minúscula: `CUPO_CAP` → `cfg.cupo_cap`).

## Endpoints
Ninguno.

## Reglas

**Split (`split.py`)**
- **R1.** `split_sale(amount, None)` → `SaleSplit(abono=0, trust_cut=0, lender_net=0, merchant_amount=amount, allocated_after=0, paid_off=False)`. El llamador pasa `None` si no hay crédito `active`.
- **R2.** Con crédito: `abono = min(bps_of(amount, pct_bps), total_repayable − allocated_total)`; `merchant_amount = amount − abono`; `allocated_after = allocated_total + abono`; `paid_off = allocated_after == total_repayable`.
- **R3.** Parte de Trust, `policy="prorated"` (default): `T(r) = r × fee_amount × trust_share_bps // (total_repayable × 10000)`; `trust_cut = T(allocated_after) − T(allocated_total)`. Sobre todo el crédito suma exactamente `bps_of(fee_amount, trust_share_bps)`.
- **R4.** `policy="literal"` (alternativa): `L(r) = bps_of(r, trust_share_bps)`; `trust_cut = min(L(allocated_after), fee_amount) − min(L(allocated_total), fee_amount)`. Es el "% de cada abono" en forma acumulada, con tope total en `fee_amount` para que el prestamista recupere siempre el principal.
- **R5.** `lender_net = abono − trust_cut`. Invariantes: `merchant_amount + lender_net + trust_cut == amount`, `0 ≤ trust_cut ≤ abono`. El llamador elige la política con `TRUST_CUT_POLICY`.

**Términos (`terms.py`)**
- **R6.** `build_terms(principal, pct_bps, fee_bps, term_months, request_pct_bps, cfg)` → `Terms` congelado con: `principal`, `fee_bps`, `fee_amount = bps_of(principal, fee_bps)`, `total_repayable = principal + fee_amount`, `trust_share_bps = cfg.trust_share_bps`, `term_months`, `monthly_minimum = ceil(total_repayable / term_months)` (entero: `-(-total // term_months)`), `pct_initial_bps = pct_bps`, `pct_max_bps = max(pct_bps, request_pct_bps)` si viene `request_pct_bps`, si no `pct_bps + cfg.reprice_max_delta_bps`, `pct_min_bps = max(500, pct_bps − 500)`. `Terms.to_dict()` da el bloque `terms` del snapshot que 11 guarda en `loans.terms`: los campos de `Terms` con los montos como enteros en unidades mínimas (las funciones puras no conocen el asset; pasarlos a Money es de 11).
- **R7.** `principal ≤ 0`, `fee_bps < 0` o `term_months < 1` → `ValueError`.

**Matching (`matching.py`)**
- **R8.** `fits(request, offer, cupo_disponible, step)`; `request` y `offer` son cualquier objeto con `min_amount`, `max_amount`, `pct_bps`. `overlap_min = max(mins)`, `overlap_max = min(maxs)`. Devuelve `None` si `overlap_min > overlap_max`, si `overlap_min > cupo_disponible` o si `offer.pct_bps > request.pct_bps`.
- **R9.** `mid = (overlap_min + overlap_max) // 2`; `amount = mid − mid % step`, recortado a `[overlap_min, min(overlap_max, cupo_disponible)]`. `Proposal(amount, overlap_min, overlap_max, pct_bps=offer.pct_bps)`.

**Recálculo (`repricing.py`)**
- **R10.** `review_period(...)`: si `balance_at_period_start == 0` → `ReviewDecision(pct_bps, "unchanged", 0, False)`. Si no, `target = min(monthly_minimum, balance_at_period_start)`.
- **R11.** `allocated_in_period < target` → `new = min(pct_bps + step_bps, pct_max_bps)`, `missed=True`, reason `missed_minimum` (o `unchanged` si ya estaba en el tope).
- **R12.** `allocated_in_period × 4 ≥ target × 5` (≥ 125 %) → `new = max(pct_bps − step_bps, pct_min_bps)`, reason `ahead_of_schedule` (o `unchanged` si ya estaba en el piso), `missed=False`. En otro caso: sin cambio, `unchanged`, `missed=False`.

**Scoring (`scoring.py`, versión `v1`)**
- **R13.** `MerchantFeatures` (la llena 09-scoring; montos en asset de mercado, días/meses en `TIMEZONE`):
  `as_of` (datetime UTC del cálculo) · `first_sale_at | None` · `active_months` = meses calendario **cerrados**, desde el mes de la primera venta hasta el mes anterior a `as_of`, con ≥ `MIN_SALES_PER_MONTH` ventas · `avg_monthly_sales_3m` = Σ ventas de los 3 últimos meses cerrados // 3 · `consistency_bps` = de las 12 últimas semanas cerradas (lun–dom), fracción con ventas ≥ 50 % del promedio semanal de esas 12 (0 si no hay ventas) · `active_days_bps` = días con ≥ 1 venta entre los 90 anteriores a hoy × 10000 // 90 · `months_on_platform` = meses completos desde `first_sale_at` (0 si no hay) · `loans_paid` · `pct_increases` (cambios `missed_minimum` en sus créditos).
- **R14.** Componentes en bps: `vol = min(10000, avg × 10000 // SCORE_VOLUME_REF)`, `cons = consistency_bps`, `days = active_days_bps`, `ant = min(10000, months_on_platform × 10000 // 12)`, `pay = clamp(5000 + 2500·loans_paid − 1000·pct_increases, 0, 10000)` (5000 = neutro sin historial). `score = (3500·vol + 2500·cons + 1500·days + 1000·ant + 1500·pay) // 100000` (0–1000).
- **R15.** Grado (`grade_for(score)`): A ≥ 750, B ≥ 600, C ≥ 450, D el resto. Factor en bps: A 10000, B 7500, C 5000, D 0.
- **R16.** `eligible = active_months ≥ MIN_MONTHS`. Cupo: `raw = avg × 1500 × 25 × factor // (10000 × 10 × 10000)` (un solo floor), `cupo = min(CUPO_CAP, raw − raw % AMOUNT_STEP)`; si no es elegible, `cupo = 0`.
- **R17.** `unlocks_at`: `None` si es elegible o si `first_sale_at` es `None`; si no, 00:00 local del día 1 del mes `mes(as_of) + (MIN_MONTHS − active_months)`, en UTC (zona `cfg.timezone`, sin `clock`).
- **R18.** `reasons` (máx. 4, en este orden): no elegible → "Te faltan {n} meses de ventas con el QR de Trust." (`n = MIN_MONTHS − active_months`; con `n = 1`, "Te falta 1 mes de ventas con el QR de Trust.") (sin ventas: "Empieza a cobrar con tu QR de Trust para construir historial.") · `vol ≥ 7000` → "Buen volumen de ventas en los últimos 3 meses." / si no "Volumen de ventas bajo en los últimos 3 meses." · `cons ≥ 7500` → "Ventas constantes semana a semana." / si no "Ventas irregulares entre semanas." · `loans_paid > 0` → "Has pagado {k} créditos con Trust." (con `k = 1`, "Has pagado 1 crédito con Trust.") / `pct_increases > 0` → "Hubo meses por debajo del abono mínimo." / si no "Sin historial de créditos: cuenta como neutro."
- **R19.** `assess(features, cfg) -> Assessment(policy_version="v1", score, grade, eligible, unlocks_at, cupo_total, reasons, features)`; `features` es el dict serializable de la entrada (`MerchantFeatures.to_dict()`, fechas ISO 8601 con `Z`; para `credit_assessments.features`). `SCORING_POLICY` distinto de `v1` → `ValueError`.

## Datos
- Ninguno (funciones puras). Los tipos (`SaleSplit`, `SplitLoanState`, `Terms`, `Proposal`, `ReviewDecision`, `MerchantFeatures`, `Assessment`) son dataclasses `frozen` en cada archivo.

## Depende de
- `00-core` (`bps_of`, config).

## Criterios de aceptación
Estado de crédito de ejemplo `L`: `pct_bps=1500`, `total_repayable` $2.100.000 (`210000000`), `fee_amount` $100.000 (`10000000`), `trust_share_bps=2000`. Venta de ejemplo $48.000 (`4800000`).
- [x] **AC-1.** Dado sin crédito, cuando `split_sale(4800000, None)`, entonces comercio $48.000 y abono, parte Trust y neto en 0 (→ `test_ac1_split_without_loan`).
- [x] **AC-2.** Dado `L` con `allocated_total=0`, cuando se divide $48.000, entonces abono $7.200, comercio $40.800, parte Trust $68,57, neto $7.131,43, `allocated_after` $7.200, `paid_off` falso (→ `test_ac2_split_basic`).
- [x] **AC-3.** Dado `L` con `allocated_total` $2.095.000, cuando se divide $48.000, entonces abono $5.000, comercio $43.000, `allocated_after` $2.100.000 y `paid_off` verdadero (→ `test_ac3_last_abono_capped`).
- [x] **AC-4.** Dado `L`, cuando se dividen ventas de $48.000 hasta `paid_off`, entonces Σ abonos $2.100.000, Σ parte Trust $20.000 (`bps_of(10000000, 2000)`), Σ netos $2.080.000; y en 1.000 créditos aleatorios (semilla fija) cada venta cumple R5 y la Σ de Trust es exacta (→ `test_ac4_prorated_sums_exactly`).
- [x] **AC-5.** Dado `L` con `policy="literal"`, cuando se divide la primera venta de $48.000, entonces parte Trust $1.440; y al pagar todo, Σ parte Trust $100.000 y Σ netos $2.000.000 (= principal) (→ `test_ac5_literal_capped_at_fee`).
- [x] **AC-6.** Dado `build_terms(200000000, 1500, 500, 3, 2000)` (principal $2.000.000), entonces cargo $100.000, total $2.100.000, mínimo $700.000, pct 1500/1000/2000, `trust_share_bps` 2000; y `build_terms(100000000, 1500, 1000, 3, None)` (principal $1.000.000) da total $1.100.000, mínimo $366.666,67, `pct_max` 2000 (→ `test_ac6_build_terms`).
- [x] **AC-7.** Dada solicitud $1.500.000–$3.000.000 @1500 y oferta $1.000.000–$2.500.000 @1500, cuando `fits(..., cupo=600000000, step=5000000)` (cupo $6.000.000, paso $50.000), entonces `Proposal` con monto $2.000.000, cruce $1.500.000–$2.500.000 y pct 1500; con cupo $1.800.000 → monto $1.800.000; con cupo $1.000.000, oferta @2000 o solicitud $3.000.000–$4.000.000 → `None` (→ `test_ac7_fits`).
- [x] **AC-8.** Dado pct 1500 (min 1000, max 2000), mínimo $700.000, saldo $2.100.000, cuando lo asignado es $500.000 → 1750 `missed_minimum` missed; $875.000 → 1250 `ahead_of_schedule`; $700.000 → 1500 `unchanged`; pct 2000 y $100.000 → 2000 `unchanged` missed; saldo $300.000 → objetivo $300.000; saldo 0 → `unchanged` (→ `test_ac8_review_period`).
- [x] **AC-9.** Dado `avg_monthly_sales_3m` $16.000.000 (`1600000000`), `consistency_bps` 10000, `active_days_bps` 8000, `months_on_platform` 6, `active_months` 6, sin créditos, cuando `assess` con la config por defecto (`SCORE_VOLUME_REF` $10.000.000 → `vol` 10000), entonces score 845, grado A, elegible, cupo $6.000.000; con `CUPO_CAP=500000000` ($5.000.000) → $5.000.000 (→ `test_ac9_assess_grade_a_cupo`).
- [x] **AC-10.** Dado `active_months=1` y `as_of` 16 oct 2026, cuando `assess`, entonces no elegible, cupo 0, `unlocks_at` `2026-12-01T05:00:00Z` y la primera razón "Te faltan 2 meses…"; y `grade_for`: 750 → A, 749 → B, 450 → C, 449 → D, con cupo 0 para D (→ `test_ac10_assess_not_eligible_and_grades`).

## Fuera de alcance
- Otras versiones de scoring, montos en otros assets y redondeos distintos al floor.
