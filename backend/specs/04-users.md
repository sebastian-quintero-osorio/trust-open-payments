# 04 · Usuarios, login y wallet propia

Estado: ☑ hecho · Fase: 0 · Dueño: Claude · Carpeta: `app/modules/users/`

## Objetivo
Registro, login con JWT (web y móvil), el perfil `/me` que pinta los encabezados de la app ("Hola, Marta", avatar "TL") y la vinculación de la wallet propia (negocio) o de cobro (prestamista).

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
|  | `POST /api/v1/auth/register` | público | `{email, password, role: merchant\|lender, first_name, last_name, city, business_name?}` | 201 `{access_token, token_type: "bearer", user: Me}` | 409 `already_exists`, 422 `validation_error` |
| ★ | `POST /api/v1/auth/login` | público | `{email, password}` | 200 `{access_token, token_type: "bearer", user: Me}` | 401 `invalid_credentials`, 422 `validation_error` (forma) |
| ★ | `GET /api/v1/me` | cualquiera | — | 200 `Me` | 401 `unauthorized` |
|  | `PATCH /api/v1/me` | cualquiera | `{first_name?, last_name?, city?, lender_range?: {min: Money, max: Money} \| null}` | 200 `Me` | 422 `validation_error` / `asset_mismatch` |
| ★ | `PUT /api/v1/me/payout-wallet` | merchant, lender | `{wallet_address}` | 200 `Me` | 403 `forbidden`, 409 `active_loan_exists`, 422 `wallet_not_found` / `invalid_url` / `asset_mismatch`, 502/503 `op_unavailable` |

```json
// Me (negocio)
{"id": "…", "email": "marta@demo.trust", "role": "merchant",
 "first_name": "Marta", "last_name": "Rojas", "business_name": "Tienda La Esquina", "city": "Bucaramanga",
 "initials": "TL", "public_name": "Marta R.",
 "payout_wallet": {"url": "https://ilp.interledger-test.dev/tienda-la-esquina", "public_name": "Tienda La Esquina", "asset_code": "COP", "asset_scale": 2},
 "lender_range": null,
 "collection_account": {"id": "…", "status": "active", "qr_payload": "https://pay.trustservices.com.co/qr/2c4e…"},
 "unread_notifications": 2}
// Me (prestamista): business_name null, initials "CP", collection_account null,
// "lender_range": {"min": {"value": "100000000", "asset_code": "COP", "asset_scale": 2}, "max": {"value": "250000000", "asset_code": "COP", "asset_scale": 2}}
```

## Reglas
- **R1.** Email se guarda y se compara en minúsculas (login insensible a mayúsculas). `first_name`, `last_name` y `business_name` de 1 a 120 caracteres y `city` de 1 a 80 (tras `strip()`), sin `\u0000`; si no, 422 `validation_error` (register y PATCH). Contraseña de 8 a 72 bytes UTF-8, con bcrypt (`core/security.py`); fuera de ese rango, 422 `validation_error` en register. En login, una contraseña de más de 72 bytes responde 401 `invalid_credentials` sin llamar a bcrypt.
- **R2.** Register: `role` solo `merchant` o `lender` (`admin` → 422 `validation_error`); `business_name` obligatorio si es `merchant` (422) e ignorado si es `lender`; email repetido → 409 `already_exists`. Devuelve token, igual que login.
- **R3.** Login: email inexistente o contraseña errada → 401 `invalid_credentials`, mismo mensaje en los dos casos. JWT según `00-core` (`sub`, `role`, `exp`).
- **R4. `initials`**: negocio → regla de `MerchantRef` sobre `business_name`; prestamista y admin → inicial de `first_name` + inicial de `last_name`, en mayúscula. **`public_name`**: regla de `LenderRef` para todos los roles.
- **R5.** `payout_wallet` = `null` si `payout_wallet_url` es null. `collection_account` = resumen `{id, status, qr_payload}` de la cuenta `active` del negocio (o la más reciente) o `null`; siempre `null` para no negocios. `unread_notifications` = conteo de `notifications` con `read_at` null.
- **R6. PATCH**: solo `first_name`, `last_name`, `city` y `lender_range`; campos vacíos → 422. `business_name` y `email` no se editan (el `display_name` del QR en Go no cambia); si llegan, se ignoran. `lender_range` solo para `lender` (otro rol que lo envíe, aunque sea `null` → 422 `validation_error`); `min` y `max` en el asset de mercado (422 `asset_mismatch`) con `0 < min ≤ max` (422 `validation_error`); `null` lo borra.
- **R7. PUT payout-wallet**, en este orden:
  1. Rol `admin` → 403 `forbidden`.
  2. Si el usuario tiene un crédito no terminal (como `merchant_id` o `lender_id`) → 409 `active_loan_exists`, **sin** llamar a Go.
  3. Una dirección con `\u0000` o de más de 2048 caracteres → 422 `invalid_url` sin llamar a Go. Si no, commit/cierre de la transacción y `op.resolve_wallet_address(wallet_address)` (acepta `https://…` o `$host/path`).
  4. Negocio: `asset_code` y `asset_scale` deben ser los de mercado (COP/2; una wallet COP con escala 0 también es `asset_mismatch`) → 422 `asset_mismatch`. Prestamista: cualquier asset.
  5. En una transacción nueva, revalida el paso 2 y guarda `payout_wallet_url` (= `url` exacta que devuelve Go), `payout_wallet_public_name`, `payout_asset_code`, `payout_asset_scale`; si cambió, `record_event(db, "user", id, "payout_wallet_set", {url, asset_code, asset_scale}, actor_id=id)`.
- **R8. Errores de Go**: `OpError` 422 `wallet_not_found` / `invalid_url` → 422 con el mismo código; otro `OpError` → 502 `op_unavailable`; `OpUnavailable` → 503 `op_unavailable`. Ante cualquier error no se guarda nada.
- **R9.** Repetir el PUT con la misma wallet es idempotente (200, mismo resultado).

## Datos
- Escribe: `users`. Lee: `loans` (no terminales), `collection_accounts`, `notifications` (definidas en `01-data-model.md`).

## Depende de
- `00-core` (`core/security.py`, `current_user`, `require_role`, formato de errores) · `02-op-client` (`resolve_wallet_address`).

## Criterios de aceptación
- [x] **AC-1.** Dado un negocio con contraseña `trust2026`, `POST /auth/login` → 200, `token_type == "bearer"`, `user.role == "merchant"` y el JWT tiene `sub` = id del usuario (→ `test_ac1_login_ok`).
- [x] **AC-2.** Contraseña errada o email inexistente → 401 `invalid_credentials` con el mismo mensaje (→ `test_ac2_login_invalid`).
- [x] **AC-3.** Dado `marta@demo.trust`, login con `MARTA@Demo.Trust` → 200 (→ `test_ac3_login_case_insensitive`).
- [x] **AC-4.** Dado el negocio "Tienda La Esquina" de Marta Rojas sin wallet ni cuenta y con 2 avisos sin leer, `GET /me` → `initials "TL"`, `public_name "Marta R."`, `payout_wallet null`, `collection_account null`, `unread_notifications 2`; para Camilo Pardo, `initials "CP"` (→ `test_ac4_me_shape`).
- [x] **AC-5.** Register de `merchant` sin `business_name` → 422 `validation_error`; con `role: admin` → 422; email ya usado (otra capitalización) → 409 `already_exists` (→ `test_ac5_register_rules`).
- [x] **AC-6.** Dado un negocio, `PUT /me/payout-wallet {"wallet_address": "$ilp.interledger-test.dev/tienda"}` → 200 y `payout_wallet.url == "https://ilp.interledger-test.dev/tienda"`, asset COP/2 (→ `test_ac6_payout_wallet_ok`).
- [x] **AC-7.** Dada una wallet USD/2 en el fake (`set_wallet(url, "USD", 2)`), el negocio recibe 422 `asset_mismatch` y no se guarda nada (igual con una wallet COP/0); el prestamista con la misma wallet USD recibe 200 con `asset_code "USD"` (→ `test_ac7_payout_wallet_asset_rules`).
- [x] **AC-8.** Con `set_wallet(url, exists=False)` → 422 `wallet_not_found`; con `fail_next("resolve_wallet_address", OpUnavailable("x"))` → 503 `op_unavailable` (→ `test_ac8_payout_wallet_op_errors`).
- [x] **AC-9.** Dado un crédito `pending_disbursement` del negocio, el PUT → 409 `active_loan_exists` y `fake_op.calls_to("resolve_wallet_address") == []` (→ `test_ac9_payout_wallet_blocked_by_loan`).
- [x] **AC-10.** Prestamista: `PATCH /me {lender_range: {min: cop(1_000_000), max: cop(2_500_000)}}` ($1.000.000–$2.500.000) → 200 y `GET /me` lo muestra; `min > max` → 422 `validation_error`; un negocio enviando `lender_range` → 422 (→ `test_ac10_patch_lender_range`).

## Fuera de alcance
- Recuperar contraseña, verificar email, refresh tokens, logout del lado servidor.
- Probar que el usuario es dueño de la wallet (Go solo la resuelve) y KYC.
