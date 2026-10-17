# 03 · Plataforma y endpoints de demo

Estado: ☐ pendiente · Fase: 0 · Dueño: — · Carpeta: `app/modules/platform/`

## Objetivo
Salud y configuración pública para web, móvil y despliegue, más los atajos de demo (`/dev/*`): sembrar datos, simular ventas Bre-B, forzar el recalculo del % y aprobar pagos con la wallet falsa.

Escala de montos: unidades mínimas del asset de mercado (USD, escala 2) = cifras de COPY.md ÷ 10. Venta $48.000 → `4800`; cupo $6.000.000 → `600000`.

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `GET /api/v1/health` | público | — | `Health` (abajo) | 503 con el mismo cuerpo y `status: "down"` si la base no responde |
|  | `GET /api/v1/config` | público | — | `Config` (abajo) | — |
| ★ | `POST /api/v1/dev/seed` | público (solo dev) | `{"with_active_loan_for_marta": false}` (opcional) | 200 `SeedResult` | 404 `not_found` (dev apagado), 409 `pool_exhausted`, 502/503 `op_unavailable` |
| ★ | `POST /api/v1/dev/sales/simulate` | público (solo dev) | `{merchant_id? \| collection_account_id?, amount: Money, payer_bank?}` | 201 `{sim_payment_id, bre_b_reference, status, processed, sale_id}` | 404 `not_found`, 409 `collection_account_not_active` / `sim_disabled` / `sim_payer_not_authorized`, 422 `validation_error` / `asset_mismatch` / `invalid_amount`, 502/503 `op_unavailable` |
|  | `POST /api/v1/dev/loans/{loan_id}/close-period` | público (solo dev) | — | 200 `LoanPctChange` | 404 `not_found`, 409 `invalid_state` |
|  | `GET /api/v1/dev/fake-wallet/authorize/{payment_id}?decision=approve\|reject` | público (dev + `OP_CLIENT=fake`) | — | 302 a `return_url`; sin `decision`, HTML con "Aprobar" / "Rechazar" | 404 `not_found`, 422 `validation_error` (`decision` inválido) |

```json
// Health
{"status": "ok", "db": "ok", "op": "ok", "op_client": "fake", "version": "0.1.0", "time": "2026-10-17T15:42:00Z"}
// Config
{"market_asset": {"asset_code": "USD", "asset_scale": 2}, "pct_options_bps": [1000, 1500, 2000],
 "min_loan_amount": {"value": "50000", "asset_code": "USD", "asset_scale": 2},
 "amount_step": {"value": "5000", "asset_code": "USD", "asset_scale": 2},
 "cupo_cap": {"value": "1000000", "asset_code": "USD", "asset_scale": 2},
 "default_term_months": 3, "timezone": "America/Bogota", "dev_endpoints_enabled": false,
 "example_sale": {"value": "4800", "asset_code": "USD", "asset_scale": 2},
 "example_splits": [{"pct_bps": 1500, "abono": {"value": "720", "asset_code": "USD", "asset_scale": 2},
                     "merchant": {"value": "4080", "asset_code": "USD", "asset_scale": 2}}]}
// SeedResult
{"users": [{"id": "…", "email": "marta@demo.trust", "password": "trust2026", "role": "merchant", "label": "Tienda La Esquina"}],
 "seed_sales": 1680, "open_offers": 4, "loans": 2}
```

## Reglas
- **R1. Health**: `SELECT 1` y `OpClient.health()`. Base OK + Go OK → `ok`. `OpUnavailable` u `OpError` en Go → `status: "degraded"`, `op: "down"`, **HTTP 200**. Si Go responde 200 con `status` distinto de `ok` → `op: "degraded"`, `status: "degraded"`, HTTP 200. Base caída → 503, `status: "down"`.
- **R2. Config**: sale de `core/config.py`. `example_sale` es la constante 4800 en el asset de mercado; `example_splits` trae un elemento por cada `PCT_OPTIONS_BPS` con `abono = bps_of(4800, pct)` y `merchant = 4800 − abono` (1000 → 480/4320, 1500 → 720/4080, 2000 → 960/3840).
- **R3. Guardas**: router `/dev` con la dependencia `require_dev()`: si `DEV_ENDPOINTS_ENABLED` es false → 404 `not_found`. `fake-wallet` exige además `OP_CLIENT=fake`.
- **R4. Seed idempotente**: busca por email; nunca duplica usuarios, cuentas, ventas, ofertas ni créditos. Una segunda corrida devuelve los mismos ids y no crea filas.
- **R5. Usuarios** (contraseña `DEMO_PASSWORD` para todos):

  | Email | Rol | Nombre | Negocio · ciudad | Wallet propia |
  |---|---|---|---|---|
  | marta@demo.trust | merchant | Marta Rojas | Tienda La Esquina · Bucaramanga | `DEMO_MERCHANT_WALLET_URL` |
  | trigal@demo.trust | merchant | Lucía Gómez | Panadería El Trigal · Bucaramanga | placeholder |
  | nova@demo.trust | merchant | Andrés Niño | Papelería Nova · Bucaramanga | placeholder |
  | camilo@demo.trust | lender | Camilo Pardo | — · Bogotá · rango 100.000–250.000 | `DEMO_LENDER_WALLET_URL` |
  | admin@demo.trust | admin | Operador Trust | — · Bogotá | — |

  Placeholder = `https://ilp.interledger-test.dev/demo-<slug>`. Las wallets se escriben directo en `users` (sin llamar a Go) con el asset de mercado y `public_name` = negocio o nombre completo.
- **R6. Recaudo**: commit de los usuarios y después `ensure_collection_account(db, op, merchant)` para los 3 negocios (nunca dentro de una transacción abierta). Hasta que exista 06, el seed omite este paso y también R7 y R9, porque `sales` necesita la cuenta.
- **R7. Historial** (`sales` con `source=seed`, `status=processed`, `puntored_event_id` null, `bre_b_reference = SEED-<slug>-<yyyymmdd>-<n>`, `paid_at` entre 7:00 y 20:00 hora local): generador determinista (`random.Random(42)`), ventas casi todos los días, desde el primer día del 4.º mes calendario anterior hasta **ayer** (nada hoy). Tienda La Esquina: promedio 1.600.000 por mes en los 3 meses completos anteriores (con la política v1 → grado A, cupo 600.000). El Trigal y Nova: ≈ 1.000.000 y 1.200.000 por mes. Las ventas seed no tocan `ledger_in` ni crean `payouts`. Si 09 da otro cupo, se calibra el seed, no la política.
- **R8. Ofertas** (`loan_offers` `open`, de Camilo, `fee_bps` 600, `term_months` 3): 100.000–250.000 · 1500 (la del match); 150.000–300.000 · 1500; 50.000–200.000 · 1000; 100.000–200.000 · 2000. Las tres primeras encajan con una solicitud 150.000–300.000 · 1500 ("3 ofertas encajan", COPY 13b); con el crédito de Marta activo quedan 3 que encajan con la solicitud virtual de 13 R4 ("Ofertas para ti (3)", COPY 13a). Marta **no** tiene solicitud ni crédito sembrado: los crea en vivo.
- **R9. Créditos sembrados** (se implementa cuando existan 05, 09 y 11; antes el seed los omite y devuelve `loans: 0`): El Trigal 150.000 · 1500 y Nova 200.000 · 1000 con Camilo, `status active`, `origin offer_taken` con su propia oferta `taken`, términos de `build_terms`, `credit_assessment_id` de `get_credit_profile`, wallets congeladas, `activated_at` = hace 15 días, `period_start` = `activated_at`, `next_review_at` = +30 días. Ventas seed posteriores con su fila en `repayments` (vía `split_sale` con `TRUST_CUT_POLICY`) hasta ≈ 58 % y 12 % de `total_repayable`. `allocated_total = Σ abono`, `trust_earned = Σ trust_cut`, `delivered_total = allocated_total`, `trust_pending = 0`. Sin `payouts`. Con el crédito en vivo de Marta (200.000) el portafolio suma 550.000.
- **R10.** `with_active_loan_for_marta: true` (para capturas): crea igual el crédito de Marta, 200.000 · 1500, desde la primera oferta (pasa a `taken`), como R9, con `allocated_total` ≈ 32 % de `total_repayable` (≈ 67.840 de 212.000, porque la oferta tiene cargo 600; COPY muestra $640.000 porque no tiene cargo).
- **R11. Simular venta**: exactamente uno de `merchant_id` / `collection_account_id` (si no, 422 `validation_error`); cuenta `active` del negocio (404 `not_found` si no hay); `amount` en el asset de mercado (422 `asset_mismatch`, sin llamar a Go). Llama `create_sim_qr_payment` con clave `sim:{uuid4}`. Con `OP_CLIENT=fake` procesa de una vez `process_puntored_event(db, sim.webhook_event (dict), source="reconciliation")` y devuelve `processed: true` y `sale_id`; con `http` devuelve `processed: false`, `sale_id: null` (la venta llega por webhook o reconciliación). Antes de llamar a Go valida `amount.value > 0` y `payer_bank` ≤ 60 caracteres (422 `validation_error`). Errores de Go: `OpError` 404 → 404 `not_found`; 409 `collection_account_not_active` / `sim_disabled` / `sim_payer_not_authorized` → 409 con el mismo código; 422 `asset_mismatch` / `invalid_amount` → 422 con el mismo código; `OpError` reintentable u otro (400, `idempotency_conflict`) → 502 `op_unavailable`; `OpUnavailable` → 503 `op_unavailable`.
- **R12. Cerrar periodo**: `close_period(db, loan_id, clock.now())` y devuelve la fila como `LoanPctChange` (`{id, old_pct_bps, new_pct_bps, reason, period_start, period_end, allocated_in_period: Money, target_amount: Money, created_at}`, igual que 11). Crédito inexistente → 404 `not_found`; crédito no `active` (`ValueError` de 11 R15) → 409 `invalid_state`.
- **R13. Wallet falsa**: pago desconocido → 404. `approve` → `fake.approve_payment`, `reject` → `fake.reject_payment`, solo si está en `awaiting_authorization`; si no, no cambia nada. Siempre 302 a `get_payment_request(id).return_url` **agregando** `op_resource=payment`, `op_id`, `op_status` (estado actual) a la query existente (`parse_qsl` + `urlencode`), sin borrar `loan_id` ni `client`.

## Datos
- Escribe: `users`, `sales`, `loan_offers`, `loans`, `repayments` (seed). Vía otras specs: `collection_accounts` (06), `credit_assessments` (09), `loan_pct_changes` (11), `webhook_events` / `sales` / `payouts` (07).

## Depende de
- `02-op-client` (`health`, `create_sim_qr_payment`, controles del fake) · `00-core` (`core/security.py` para el hash) · `06-recaudo` (`ensure_collection_account`) · `07-sales` (`process_puntored_event`) · `05-policies` (`build_terms`, `split_sale`) · `09-scoring` (`get_credit_profile`) · `11-loans` (`close_period`).

## Criterios de aceptación
- [ ] **AC-1.** Dada la app arrancada con el fake, `GET /health` → 200 `{status: "ok", db: "ok", op: "ok"}` (→ `test_ac1_health_ok`).
- [ ] **AC-2.** Dado `fake_op.fail_next("health", OpUnavailable("down"))`, `GET /health` → **200** con `status: "degraded"` y `op: "down"` (→ `test_ac2_health_degraded`).
- [ ] **AC-3.** `GET /config` trae `pct_options_bps == [1000,1500,2000]` y para 1500 `abono.value == "720"` y `merchant.value == "4080"` (→ `test_ac3_config`).
- [ ] **AC-4.** Con `DEV_ENDPOINTS_ENABLED=false`, `POST /dev/seed` → 404 `not_found` (→ `test_ac4_dev_disabled`).
- [ ] **AC-5.** Dado el seed corrido dos veces, hay 5 usuarios con los mismos ids, el mismo número de ventas seed, y `POST /auth/login` de marta@demo.trust con `DEMO_PASSWORD` → 200 (→ `test_ac5_seed_idempotent`).
- [ ] **AC-6.** Tras el seed, Tienda La Esquina tiene cuenta de recaudo `active`, wallet propia, ventas seed en ≥ `MIN_MONTHS` meses calendario, ninguna hoy, promedio de los 3 meses completos anteriores 1.600.000 ± 1 %, y `ledger_in == 0` (requiere 06; `skip` hasta entonces) (→ `test_ac6_seed_history`).
- [ ] **AC-7.** Tras el seed, Camilo tiene 4 ofertas `open` y Marta no tiene solicitud ni crédito (→ `test_ac7_seed_offers_no_marta_loan`).
- [ ] **AC-8.** Dado el seed y el fake, `POST /dev/sales/simulate {merchant_id: marta, amount: 4800 USD}` → 201 con `processed: true`, existe una venta `live` de 4800 y el fake registró `create_sim_qr_payment` con clave que empieza por `sim:` (requiere 06 y 07) (→ `test_ac8_simulate_sale_fake`).
- [ ] **AC-9.** Con `amount` en EUR → 422 `asset_mismatch` y `fake_op.calls_to("create_sim_qr_payment") == []` (→ `test_ac9_simulate_asset_mismatch`).
- [ ] **AC-10.** Dado un pago del fake con `return_url = https://app.trustservices.com.co/op/return?client=web&loan_id=L`, `GET /dev/fake-wallet/authorize/{id}?decision=approve` → 302 con `client=web`, `loan_id=L`, `op_resource=payment`, `op_id={id}` y `op_status=processing`; el pago queda `processing` (→ `test_ac10_fake_wallet_authorize`).
- [ ] **AC-11.** Dado el seed con `with_active_loan_for_marta: true`, `POST /dev/loans/{loan_id}/close-period` → 200 y hay una fila nueva en `loan_pct_changes` (requiere 05, 09 y 11) (→ `test_ac11_dev_close_period`).

## Fuera de alcance
- Proteger `/dev/*` con auth (en producción el flag va apagado).
- Borrar o reiniciar datos (el seed solo agrega lo que falta).
