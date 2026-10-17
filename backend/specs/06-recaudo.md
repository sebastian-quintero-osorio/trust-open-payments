# 06 · Cuenta de recaudo

Estado: ☐ pendiente · Fase: 1 · Dueño: — · Carpeta: `app/modules/recaudo/`

## Objetivo
Asignar a cada negocio su cuenta de recaudo Trust (la wallet del pool de Go y su QR Bre-B simulado) y mostrarla con el saldo que lleva el libro de Trust.

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| ★ | `GET /api/v1/me/collection-account` | merchant | — | 200 `CollectionAccountOut` | 404 `not_found` |
|  | `POST /api/v1/me/collection-account` | merchant | — | 201 (nueva) o 200 (ya existía) `CollectionAccountOut` | 422 `wallet_required`, 409 `pool_exhausted`, 502/503 `op_unavailable` |

```json
// CollectionAccountOut
{"id": "…", "wallet_address": "https://ilp.interledger-test.dev/trust-recaudo-01",
 "qr_payload": "https://pay.trustservices.com.co/qr/2c4e6a80-1b3d-4f5a-8c7e-9d0f1a2b3c4d",
 "asset": {"asset_code": "COP", "asset_scale": 2}, "status": "active",
 "balance": {"value": "4080000", "asset_code": "COP", "asset_scale": 2},
 "created_at": "2026-10-16T14:00:00Z", "revoked_at": null}
```

## Reglas
- **R1. GET**: devuelve la cuenta `active` del negocio; si no hay, la más reciente (`revoked`); si no tiene ninguna, 404 `not_found`. Solo lee la base (no llama a Go). `id` es el de Trust (`collection_accounts.id`), nunca el de Go.
- **R2. Saldo**: `balance = ledger_in − ledger_out` en el asset de la cuenta, como Money.
- **R3. POST** = `ensure_collection_account(db, op, merchant)`. El router mira antes si hay una cuenta `active`: si la había responde 200, si no 201 (la firma de la función no cambia).
- **R4. `ensure_collection_account`** (idempotente; también la usa el seed), en este orden:
  1. Si el negocio tiene una cuenta `active` → la devuelve sin llamar a Go.
  2. Sin `payout_wallet_url` → 422 `wallet_required`.
  3. Clave: `merchant:{id}:recaudo`; si el negocio ya tiene `n` cuentas `revoked`, `merchant:{id}:recaudo:{n+1}` (la clave base devolvería por 72 h la respuesta de la cuenta revocada).
  4. `db.commit()` (no queda transacción abierta) y `op.create_collection_account(CreateCollectionAccountRequest(external_ref="merchant:{id}", display_name=business_name[:100], asset_code=MARKET_ASSET_CODE), idempotency_key=clave)`.
  5. `OpError 409 already_assigned` → `op.find_collection_accounts("merchant:{id}")` y toma la primera `active` (si no hay ninguna → 502 `op_unavailable`). `OpError 409 pool_exhausted` → 409 `pool_exhausted`. Otro `OpError` → 502 `op_unavailable`. `OpUnavailable` → 503 `op_unavailable`. En ningún error se guarda nada.
  6. En una transacción nueva inserta la fila (`op_collection_account_id`, `wallet_address`, `qr_payload`, `asset_code`, `asset_scale`, `status active`) y `record_event(db, "collection_account", id, "assigned", {"op_id", "wallet_address"}, actor_id=merchant.id)`; commit.
  7. Si el insert choca con un único (dos POST simultáneos reciben el mismo replay de Go): rollback, relee la cuenta `active` y la devuelve.
- **R5.** `ensure_collection_account` hace commit por dentro: quien la llama no debe tener cambios sin guardar.
- **R6.** La cuenta queda en el asset de mercado, COP/2 (la wallet propia del negocio ya lo es por `04-users`). Go elige la wallet del pool por `asset_code`; si la cuenta que devuelve (nueva o recuperada en el paso 5) trae otro `asset_code` o `asset_scale`, se guarda igual con el asset que dio Go (es la wallet real), se loguea ERROR y `GET /health` queda `degraded` con `market_asset_mismatch` (03 R1).

## Datos
- Escribe: `collection_accounts`, `events`. Lee: `users` (definidas en `01-data-model.md`).

## Depende de
- `02-op-client` (`create_collection_account`, `find_collection_accounts`) · `04-users` (wallet propia) · `00-core` (`record_event`, errores).

## Criterios de aceptación
- [ ] **AC-1.** Dado un negocio sin cuenta, `GET /me/collection-account` → 404 `not_found`; un prestamista → 403 `forbidden` (→ `test_ac1_get_without_account`).
- [ ] **AC-2.** Dado un negocio sin wallet propia, `POST` → 422 `wallet_required` y `fake_op.calls_to("create_collection_account") == []` (→ `test_ac2_wallet_required`).
- [ ] **AC-3.** Dado "Tienda La Esquina" con wallet, `POST` → 201, `status "active"`, `qr_payload` de Go; el fake recibió clave `merchant:{id}:recaudo` y cuerpo `{external_ref: "merchant:{id}", display_name: "Tienda La Esquina", asset_code: "COP"}`; hay un evento `assigned` (→ `test_ac3_create_account`).
- [ ] **AC-4.** Un segundo `POST` → 200 con el mismo `id` y sin otra llamada a Go (→ `test_ac4_post_idempotent`).
- [ ] **AC-5.** Dado que Go ya tiene una cuenta `active` para `merchant:{id}` (creada en el fake con otra clave) y la base ninguna, `POST` → 201 y guarda el `op_collection_account_id` que devuelve `find_collection_accounts` (→ `test_ac5_already_assigned_recovers`).
- [ ] **AC-6.** Con el pool del fake vacío → 409 `pool_exhausted`; con `fail_next("create_collection_account", OpUnavailable("x"))` → 503 `op_unavailable`; en los dos casos no hay filas (→ `test_ac6_op_errors`).
- [ ] **AC-7.** Dada una cuenta con `ledger_in` $48.000 y `ledger_out` $7.200, `GET` → `balance.value == "4080000"` ($40.800) (→ `test_ac7_balance`).
- [ ] **AC-8.** Dado un negocio con una cuenta `revoked`, `POST` → 201 con una cuenta nueva y el fake recibió la clave `merchant:{id}:recaudo:2` (→ `test_ac8_reassign_after_revoke`).
- [ ] **AC-9.** Dado que el insert del paso 6 choca con una fila `active` insertada en paralelo para el mismo `op_collection_account_id`, `ensure_collection_account` devuelve esa fila sin error (→ `test_ac9_concurrent_insert_returns_existing`).

## Fuera de alcance
- Revocar (es de `16-admin`) y mover un crédito activo a una cuenta nueva.
- Consultar el estado o el saldo en Go (`get_collection_account`): el saldo de cada negocio lo lleva Trust.
