# 08 · Worker: transferencias, desembolsos, reconciliación y jobs

Estado: ☑ hecho · Fase: 1 · Dueño: Claude · Carpeta: `app/worker.py`

## Objetivo
Hacer en segundo plano todo lo que depende de Go por polling: mandar las patas que crea 07, seguir su estado y la contabilidad de entrega, seguir desembolsos, vencer créditos pendientes, reconciliar webhooks perdidos y cerrar periodos.

## Endpoints
Ninguno.

## Reglas
- **R1. Loop.** Hilo daemon que arranca el lifespan si `WORKER_ENABLED`. Cada `WORKER_INTERVAL_SECONDS` abre su propia sesión y llama `run_once(db, get_op_client(), clock.now(), reconcile=due)`, donde `due` es true al arrancar y cuando pasaron ≥ 30 s desde la última reconciliación (en memoria). Se detiene con un `threading.Event` al apagar.
- **R2. Aislamiento.** `run_once` corre J1…J6 en ese orden; cada job en su propia transacción y su propio `try/except` (rollback + log). Una falla nunca detiene el loop ni los otros jobs. En J1, J2 y J3 cada fila también se aísla: la que falla no frena a las demás.
- **R3. Nunca con transacción abierta al llamar a Go.** Patrón: (a) tx corta `SELECT … FOR UPDATE SKIP LOCKED LIMIT 50`, copiar los datos y commit; (b) llamada a Go; (c) tx corta por fila: `SELECT … FOR UPDATE` en el orden de `00-core` (cuenta → crédito → pata) y aplicar el resultado solo si la fila sigue en el `status` y el `attempt` leídos en (a); si cambió, se descarta, salvo un 201, que siempre se aplica (la transferencia ya existe en Go). Tras un 409 `collection_account_not_active`, J1 no llama a Go por las demás patas de esa cuenta en ese tick.
- **R4. J1 · enviar patas.** `payouts` `pending` con `next_attempt_at` null o ≤ now, orden `created_at` y luego lender → merchant → trust. `create_transfer` con `Idempotency-Key: payout:{id}:{attempt}` y body armado solo con campos guardados: `{collection_account_id: op_collection_account_id de la cuenta de la venta, receiver_wallet_address: receiver_wallet_url, debit_amount: Money(current_debit_amount, asset), description, external_ref: "payout:{id}"}`.

  | Respuesta de Go | Efecto en la pata |
  |---|---|
  | 201 | `processing`, `op_transfer_id`, `next_attempt_at = null`, evento |
  | `OpUnavailable`, `OpError` 401 o 409 `request_in_progress` (o cualquier `OpError` con `retryable`, 02 R3) | Sigue `pending` con el mismo `attempt` (misma clave), `next_attempt_at = now + 10 s` y evento `submit_unknown` `{attempt}` (uno por intento): Go pudo haber creado la transferencia |
  | `OpError` 409 `collection_account_not_active` | Cuenta `revoked` (`revoked_at`), sus patas `pending` → `held` salvo las de envío ambiguo, y esta pata `held` (la 409 es definitiva para su clave), eventos |

  **Envío ambiguo**: una pata `pending` cuyo `attempt` actual tiene evento `submit_unknown`. Al revocar la cuenta (aquí, en R5 con `grant_revoked` y en 16 R6) **no** pasa a `held`: revocar no cancela un pago que Go ya creó. Sigue `pending` y J1 repite la misma clave: el 201 guardado → `processing` (R5 la contabiliza); una 409 `collection_account_not_active` → nada se creó con esa clave → `held`. Deja de ser ambigua al salir de `pending` o cambiar de `attempt`.
  | Otro `OpError` (400, 404, 409 `idempotency_conflict`, 422) | `failed_final`, `failure_code = code`, `last_error`, evento, `payout_failed` a cada admin |
- **R5. J2 · seguir patas.** `payouts` `processing` → `get_transfer(op_transfer_id)`. Sea `d` lo entregado en este intento: `current_debit_amount` si `completed`, `sent_amount` (o 0) si `failed` (acotado a `[0, current_debit_amount]`). Siempre: `sent_total += d`, `collection_accounts.ledger_out += d`, `loan.delivered_total += d` si la pata es `lender` o `trust`; se guardan `receive_*` (se suman entre intentos si el asset no cambia), `incoming_payment_url`, `outgoing_payment_url` y, si falló, `failure_code`/`last_error`.
  - `processing` → nada.
  - `completed` → `completed`, `completed_at` (de Go o now), evento; si es `lender`: `notify abono_received` al prestamista (datos del repayment de esa venta).
  - `failed` con `grant_revoked` → cuenta `revoked`, esta pata y las `pending` de la cuenta → `held` (esta pata queda `completed` si ya no tiene resto).
  - `failed` y `resto = amount − sent_total` es 0 → `completed` (como arriba: si es `lender`, `abono_received`).
  - `failed`, `resto > 0` y la cuenta ya no está `active` (p. ej. 16 la revocó mientras la pata estaba `processing`) → `held` en vez de reintentar (evento `held` con `reason = collection_account_not_active`).
  - `failed`, `retryable`, `resto > 0` y `attempt < 5` → `attempt += 1`, `current_debit_amount = resto`, `op_transfer_id = null`, `pending`, `next_attempt_at = now + [10 s, 1 min, 5 min, 5 min][attempt_anterior − 1]`, evento.
  - Cualquier otro `failed` → `failed_final` + `payout_failed` a cada admin. La plata queda en recaudo.
  - `get_transfer` con `OpError` 404 → `failed_final` (`failure_code = not_found`) + `payout_failed`; 401 o `retryable` → siguiente tick (otro `OpError`: log y siguiente tick). Bloquea solo la pata (no hay entrega que contabilizar).
- **R6. J3 · desembolsos.** Créditos `disbursing` → `get_payment(op_disbursement_payment_id)` → tx con el crédito `FOR UPDATE` → `on_disbursement_update(db, loan, payment)`. Las transiciones y los avisos son de 11. `OpUnavailable` u `OpError` → log (sin traceback) y siguiente tick.
- **R7. J4 · vencer.** `expire_pending(db, now)`.
- **R8. J5 · reconciliar** (solo con `reconcile=True`). `cursor = op_cursors['sim_qr_payments']` (0 si no existe) → `list_sim_qr_payments(after=cursor)` → por cada item, en orden y **en su propia transacción** (commit por item: la cuenta y el crédito se sueltan antes del siguiente; con una sola transacción, un webhook en vivo del mismo evento chocaba en deadlock): si está `completed` con `webhook_event`, `process_puntored_event(db, item.webhook_event, "reconciliation")` (si lanza: rollback, log y sigue con los demás); los `failed` se saltan. El cursor avanza solo por el tramo inicial de items terminados sin error (procesados o `failed`): queda en el `seq` anterior al primer item que lanzó o que sigue `processing` (se repite en la próxima reconciliación; el dedup por `event_id` y referencia lo hace seguro); si no hubo ninguno, `next_after`. Upsert de `op_cursors['sim_qr_payments']` y commit.
- **R9. J6 · cierre diario.** Si la hora local es ≥ 00:05 y `op_cursors['daily_close']` < hoy (`YYYYMMDD` como entero): para cada crédito `active` con `next_review_at ≤ now`, `close_period(db, loan_id, now)` en su propia tx; al final guarda `daily_close = hoy`. Sobrevive reinicios.
- **R10.** El worker no toca patas `held`, `failed_final` ni `completed`; liberarlas es de 16-admin.
- **R11. Eventos y avisos.** Eventos de pata: `processing` (201), `submit_unknown` (envío sin respuesta definitiva), `completed`, `retry_scheduled`, `held`, `failed_final`; de cuenta: `revoked` (`{reason, source: "worker"}`). `payout_failed`: `{leg}` en español (`prestamista`, `negocio`, `Trust`), `{monto}` = `amount` de la pata, `data` con `loan_id` si hay crédito. Un 201 que llega con la pata cambiada se aplica con el `attempt` y el `current_debit_amount` del envío (es la transferencia que existe en Go). J3 bloquea solo el crédito (no toca la cuenta).

## Datos
- Lee: `payouts`, `sales`, `collection_accounts`, `loans`, `repayments`, `users` (admins) · Escribe: `payouts`, `collection_accounts` (`status`, `revoked_at`, `ledger_out`), `loans` (`delivered_total`), `op_cursors` (`sim_qr_payments`, `daily_close`), `events`, `notifications`. Lo demás lo escriben 07 y 11 a través de sus funciones.

## Depende de
- `02-op-client` (`create_transfer`, `get_transfer`, `get_payment`, `list_sim_qr_payments`, `OpError`, `OpUnavailable`, FakeOpClient) · `07-sales` (patas, `process_puntored_event`) · `11-loans` (`on_disbursement_update`, `expire_pending`, `close_period`) · `14-notifications` (`notify`, textos de `abono_received` y `payout_failed`).

## Criterios de aceptación
Base: la venta del AC-1 de 07 ($48.000; patas `pending` lender $7.069,10, merchant $40.800, trust $130,90). `fake_op` se configura por test (sus controles los define 02).
- [x] **AC-1.** Cuando corre `run_once`, entonces hay 3 `create_transfer` en orden lender, merchant, trust, con claves `payout:{id}:1`, `debit_amount` = `current_debit_amount`, `description` y `external_ref` congelados, y las patas quedan `processing` con `op_transfer_id` (→ `test_ac1_submits_in_leg_order`)
- [x] **AC-2.** Dado `create_transfer` que lanza `OpUnavailable`, la pata sigue `pending` con `attempt` 1 y `next_attempt_at = now + 10 s`; `run_once` a now + 5 s no llama a Go; a now + 11 s reintenta con la misma clave `payout:{id}:1` (→ `test_ac2_unavailable_keeps_same_key`)
- [x] **AC-3.** Dado 422 `invalid_amount`, la pata queda `failed_final` con `failure_code`, hay evento y cada admin recibe `payout_failed` (→ `test_ac3_422_failed_final`)
- [x] **AC-4.** Dado 409 `collection_account_not_active`, la cuenta queda `revoked` y sus 3 patas `held` (→ `test_ac4_account_not_active_holds`)
- [x] **AC-5.** Dado `get_transfer` `completed` para las 3 (la lender con wallet en USD y `receive_amount` `{"value": "177", "asset_code": "USD", "asset_scale": 2}`), quedan `completed` con `sent_total = amount`, `receive_*` y URLs; `ledger_out` $48.000; `delivered_total` $7.200 (lender + trust, no merchant); el prestamista recibe un `abono_received` (→ `test_ac5_completed_updates_ledgers`)
- [x] **AC-6.** Dada la pata lender `failed` `insufficient_balance` retryable con `sent_amount` $3.000, queda `sent_total` $3.000, `ledger_out` y `delivered_total` +$3.000, `attempt` 2, `current_debit_amount` $4.069,10, `pending`, `next_attempt_at = now + 10 s`; el siguiente envío usa `payout:{id}:2` y debit $4.069,10 (→ `test_ac6_partial_failure_retries_remainder`)
- [x] **AC-7.** Fallos retryables seguidos dan backoff de 10 s, 1 min, 5 min y 5 min; el intento 5 que falla queda `failed_final` con `payout_failed` (→ `test_ac7_backoff_and_max_attempts`)
- [x] **AC-8.** Dado `failed` con `grant_revoked`, la cuenta queda `revoked`, esa pata y las `pending` de la cuenta `held` (→ `test_ac8_grant_revoked`)
- [x] **AC-9.** Dado `get_transfer` que lanza `RuntimeError`, el mismo `run_once` igual vence (J4) un crédito `pending_disbursement` con `expires_at` pasado (→ `test_ac9_job_failure_isolated`)
- [x] **AC-10.** Dado un crédito `disbursing` y `get_payment` `completed`, se llama `on_disbursement_update` una vez con ese pago y el crédito queda `active` (→ `test_ac10_polls_disbursements`)
- [x] **AC-11.** Dado `list_sim_qr_payments` con [A `completed` ya recibido por webhook, B `completed` nuevo, C `failed`] y `next_after` 7: queda 1 venta nueva con `webhook_events.source = reconciliation`, A da `duplicate`, C se salta, `op_cursors['sim_qr_payments'] = 7`; el siguiente run pide `after=7`; con `reconcile=False` no se llama (→ `test_ac11_reconciliation_cursor`)
- [x] **AC-12.** A las 00:04 locales J6 no corre; a las 00:06 llama `close_period` una vez por crédito `active` con `next_review_at ≤ now`; otro run el mismo día no repite (→ `test_ac12_daily_close_once`)
- [x] **AC-13.** Envolviendo `fake_op.create_transfer` y `fake_op.get_transfer`, en cada llamada `db.in_transaction()` es `False` (→ `test_ac13_no_open_tx_during_op_calls`)

## Fuera de alcance
- Webhooks de Go en vez de polling · varios procesos de worker · liberar o reintentar patas `held`/`failed_final` (16-admin) · alertas externas (solo eventos y avisos in-app).
