# 16 · Admin (operador de Trust)

Estado: ☐ pendiente · Fase: 3 · Dueño: — · Carpeta: `app/modules/admin/`

## Objetivo
Que el operador de Trust vea cuánto ganó Trust y cuánta plata está retenida en recaudo, y pueda reintentar una pata atascada o revocar una cuenta de recaudo.

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
|  | `GET /api/v1/admin/dashboard` | admin | — | 200 `AdminDashboard` | 401 `unauthorized`, 403 `forbidden` |
|  | `POST /api/v1/admin/payouts/{payout_id}/retry` | admin | — | 200 `PayoutOut` | 404 `not_found`, 409 `invalid_state` / `collection_account_not_active`, 422 `wallet_required` |
|  | `POST /api/v1/admin/collection-accounts/{collection_account_id}/revoke` | admin | — | 200 `{collection_account: CollectionAccountOut, held_payouts: int}` | 404 `not_found`, 502/503 `op_unavailable` |

```json
// AdminDashboard (montos agrupados por asset: una entrada por asset_code/asset_scale)
{"trust_revenue":  [{"value": "220000",  "asset_code": "COP", "asset_scale": 2}],
 "trust_pending":  [{"value": "35000",   "asset_code": "COP", "asset_scale": 2}],
 "retained_funds": [{"value": "4600000", "asset_code": "COP", "asset_scale": 2}],
 "in_flight":      [{"value": "0",       "asset_code": "COP", "asset_scale": 2}],
 "payouts_by_status": {"pending": 0, "processing": 0, "completed": 2, "failed_final": 1, "held": 1, "skipped": 0},
 "loans_by_status": {"pending_acceptance": 0, "pending_disbursement": 0, "disbursing": 0, "active": 3, "paid": 0, "cancelled": 0, "rejected": 0, "expired": 0},
 "collection_accounts": {"active": 3, "revoked": 0},
 "retained_payouts": [{"id": "…", "leg": "lender", "status": "failed_final", "failure_code": "invalid_amount",
   "amount": {"value": "720000", "asset_code": "COP", "asset_scale": 2}, "sent_total": {"value": "200000", "asset_code": "COP", "asset_scale": 2},
   "merchant": {"id": "…", "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"}}]}
// PayoutOut
{"id": "…", "sale_id": "…", "loan_id": "…", "leg": "lender", "status": "pending", "attempt": 3,
 "amount": {"value": "720000", "asset_code": "COP", "asset_scale": 2},
 "sent_total": {"value": "200000", "asset_code": "COP", "asset_scale": 2},
 "current_debit_amount": {"value": "520000", "asset_code": "COP", "asset_scale": 2},
 "failure_code": null, "next_attempt_at": "2026-10-17T15:42:00Z"}
```

## Reglas
- **R1.** Todo el router exige `require_role("admin")`.
- **R2. Partición del dinero de las patas** (cada unidad cae en un solo grupo, agrupado por asset de la pata):
  - `trust_revenue` = Σ `sent_total` de las patas `trust` en cualquier estado (las `completed` aportan todo su monto; una fallida, lo que alcanzó a enviar).
  - `retained_funds` = Σ (`amount − sent_total`) de las patas `failed_final` o `held` (todas las patas).
  - `in_flight` = Σ (`amount − sent_total`) de las patas `pending` o `processing`.
  - Cada lista (también `trust_pending`) trae una entrada por cada asset presente en `payouts`/`loans`; si un grupo no suma nada, va `"0"` en ese asset; sin ninguna fila, una entrada `"0"` en el asset de mercado (nunca lista vacía).
- **R3.** `trust_pending` = Σ `loans.trust_pending` por asset del crédito. Los conteos por estado incluyen todos los valores del enum, también en 0.
- **R4.** `retained_payouts`: las patas `failed_final` o `held`, más recientes primero, máximo 50, con el `MerchantRef` de la venta.
- **R5. Reintentar**: bloquea (`FOR UPDATE`) la cuenta de recaudo de la venta y después la pata. Estado distinto de `failed_final` / `held` → 409 `invalid_state`; cuenta no `active` → 409 `collection_account_not_active`; `amount − sent_total ≤ 0` → 409 `invalid_state`; pata `merchant` con `receiver_wallet_url` null (07 R4): toma el `payout_wallet_url` actual del negocio o, si sigue sin wallet, 422 `wallet_required` (pata `trust` con receptor null: toma `TRUST_REVENUE_WALLET_ADDRESS`; si sigue vacío, 409 `invalid_state`). Si pasa: `status = pending`, `attempt += 1`, `current_debit_amount = amount − sent_total`, `op_transfer_id`, `failure_code` y `last_error` a null, `next_attempt_at = clock.now()`, y `record_event(db, "payout", id, "manual_retry", {"attempt", "current_debit_amount"}, actor_id=admin.id)`. El worker (08) la envía con la clave `payout:{id}:{attempt}`.
- **R6. Revocar**, en este orden:
  1. Transacción: `FOR UPDATE` de la cuenta. Si está `active`: `status = revoked`, `revoked_at = clock.now()`, sus patas `pending` (por `sales.collection_account_id`) → `held`, `record_event` `revoked` en la cuenta (`actor_id` = admin) y `held` en cada pata. Las `processing` no se tocan (su resultado llega por el GET del worker). Commit.
  2. Después del commit: `op.revoke_collection_account(op_collection_account_id)`, **también** si la cuenta ya estaba revocada (el DELETE de Go es idempotente; así repetir la llamada completa una revocación a medias).
  3. `OpError 404` en Go cuenta como revocada. Otro `OpError` → 502 `op_unavailable`; `OpUnavailable` → 503 `op_unavailable`. En los dos casos la revocación local se queda.
- **R7.** `held_payouts` = patas que pasaron a `held` en esta llamada (0 si ya estaba revocada).

## Datos
- Lee: `payouts`, `sales`, `loans`, `collection_accounts`, `users`. Escribe: `payouts`, `collection_accounts`, `events` (definidas en `01-data-model.md`).

## Depende de
- `02-op-client` (`revoke_collection_account`) · `06-recaudo` (`CollectionAccountOut`) · `08-worker` (envía las patas `pending`) · `00-core` (`record_event`, `require_role`, orden de bloqueos).

## Criterios de aceptación
- [ ] **AC-1.** Un negocio o un prestamista en cualquiera de los 3 endpoints → 403 `forbidden`; sin token → 401 (→ `test_ac1_admin_only`).
- [ ] **AC-2.** Dadas dos patas `trust` `completed` de $1.500 y $700, una `lender` `failed_final` de $7.200 con `sent_total` $2.000 y una `merchant` `held` de $40.800, el dashboard da `trust_revenue` $2.200, `retained_funds` $46.000 ($5.200 + $40.800), `in_flight` $0 y `payouts_by_status` `{completed: 2, failed_final: 1, held: 1, …: 0}` (→ `test_ac2_dashboard_money`).
- [ ] **AC-3.** Dados créditos con `trust_pending` $200 y $150 en COP, `trust_pending == [cop(350)]` (`"35000"`), y `loans_by_status` trae las 8 claves (→ `test_ac3_dashboard_loans`).
- [ ] **AC-4.** Dada la pata `failed_final` de $7.200 con `sent_total` $2.000 y `attempt` 2, `retry` → 200, `status "pending"`, `attempt 3`, `current_debit_amount` $5.200 (`"520000"`), `op_transfer_id null` y un evento `manual_retry` con el `actor_id` del admin (→ `test_ac4_retry_payout`).
- [ ] **AC-5.** `retry` de una pata `completed` → 409 `invalid_state`; de una `held` cuya cuenta está `revoked` → 409 `collection_account_not_active`; de una `merchant` `held` con `receiver_wallet_url` null y el negocio aún sin wallet → 422 `wallet_required` (si ya la vinculó, 200 y la pata toma esa wallet) (→ `test_ac5_retry_rejections`).
- [ ] **AC-6.** Dada una cuenta `active` con 2 patas `pending` y 1 `processing`, `revoke` → 200, `held_payouts 2`, cuenta `revoked` con `revoked_at`, la `processing` igual, y el fake registró `revoke_collection_account(op_id)` (→ `test_ac6_revoke_account`).
- [ ] **AC-7.** Con `fail_next("revoke_collection_account", OpUnavailable("x"))` → 503 `op_unavailable` y la cuenta queda `revoked` en la base; repetir → 200, `held_payouts 0`, y Go se llamó otra vez (→ `test_ac7_revoke_commit_before_op`).
- [ ] **AC-8.** `retry` o `revoke` de un id inexistente → 404 `not_found` (→ `test_ac8_admin_not_found`).

## Fuera de alcance
- Reasignar un crédito activo a la cuenta nueva del negocio, reversos y ajustes manuales del libro.
- Pantalla web de admin (basta `/docs` o curl en la demo).
