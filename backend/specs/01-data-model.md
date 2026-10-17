# 01 · Modelo de datos (todas las tablas)

Estado: ☐ pendiente · Fase: 0 · Dueño: — · Carpeta: `app/modules/<dueño>/models.py`

Fuente única de tablas, columnas y enums. Las specs de módulo **no** las redefinen. Si hace falta una columna, se agrega aquí primero.

Convenciones: `id uuid pk` (uuid4) y `created_at` / `updated_at timestamptz` (UTC) en todas las tablas, salvo que se diga otra cosa. Los montos son `bigint` en unidades mínimas, con `asset_code text` + `asset_scale smallint` en la misma fila. Los enums se guardan como `text` con `CHECK`.

## Enums
| Enum | Valores |
|---|---|
| `user_role` | `merchant`, `lender`, `admin` |
| `collection_account_status` | `active`, `revoked` |
| `grade` | `A`, `B`, `C`, `D` |
| `request_status` | `open`, `matched`, `cancelled` |
| `offer_status` | `open`, `taken`, `cancelled` |
| `loan_origin` | `offer_taken` (el negocio tomó una oferta), `request_funded` (el prestamista fondeó una solicitud), `match` (solicitud + oferta) |
| `loan_status` | No terminales: `pending_acceptance`, `pending_disbursement`, `disbursing`, `active`. Terminales: `paid`, `cancelled`, `rejected`, `expired` |
| `pct_change_reason` | `missed_minimum`, `ahead_of_schedule`, `unchanged`, `manual` |
| `sale_source` | `live` (webhook o reconciliación), `seed` (historial sembrado, sin patas) |
| `sale_status` | `processed`, `held` (cuenta no activa: patas retenidas) |
| `payout_leg` | `lender`, `merchant`, `trust` |
| `payout_status` | `pending`, `processing`, `completed`, `failed_final`, `held`, `skipped` |
| `webhook_status` | `processed`, `unmatched`, `ignored` |
| `waitlist_tipo` | `comercio`, `prestamista` |

## Máquina de estados del crédito (compartida por 08-worker, 07-sales y 11-loans)
| Desde | Evento | Hacia | Quién |
|---|---|---|---|
| — | negocio crea (`offer_taken` o `match`) | `pending_disbursement` | 11-loans |
| — | prestamista crea (`request_funded` o `match`) | `pending_acceptance` | 11-loans |
| `pending_acceptance` | negocio acepta / rechaza | `pending_disbursement` / `rejected` | 11-loans |
| `pending_acceptance`, `pending_disbursement` | cualquiera de los dos cancela | `cancelled` | 11-loans |
| `pending_disbursement` | prestamista rechaza | `rejected` | 11-loans |
| `pending_acceptance`, `pending_disbursement` | pasa `expires_at` | `expired` | 08-worker (vía `expire_pending` de 11) |
| `pending_disbursement` | Go acepta `POST /v1/payments` (201) | `disbursing` | 11-loans |
| `disbursing` | pago Go `completed` | `active` | 08-worker (vía `on_disbursement_update` de 11) |
| `disbursing` | pago Go `failed` / `rejected` / `expired` | `pending_disbursement` (sube `disbursement_attempt`) | 08-worker (vía `on_disbursement_update` de 11) |
| `active` | `allocated_total == total_repayable` | `paid` | 07-sales |

Al pasar a `cancelled`, `rejected` o `expired`, la solicitud vuelve a `open` y la oferta vuelve a `open`, si siguen existiendo y no fueron canceladas por su dueño.

## Tablas

### `users` · dueño: 04-users
| Columna | Tipo | Notas |
|---|---|---|
| email | text | único, guardado en minúsculas |
| password_hash | text | bcrypt |
| role | `user_role` | |
| first_name, last_name | text | |
| business_name | text null | obligatorio si `role = merchant` |
| city | text | |
| payout_wallet_url | text null | `url` que devuelve Go (id exacto de la wallet) |
| payout_wallet_public_name | text null | |
| payout_asset_code, payout_asset_scale | text null, smallint null | |
| lender_range_min, lender_range_max | bigint null | "Mi rango" del prestamista (asset de mercado) |

### `collection_accounts` · dueño: 06-recaudo
| Columna | Tipo | Notas |
|---|---|---|
| merchant_id | fk users | índice único parcial: uno `active` por negocio |
| op_collection_account_id | uuid | único (id en Go) |
| wallet_address, qr_payload | text | |
| asset_code, asset_scale | | |
| status | `collection_account_status` | |
| ledger_in | bigint default 0 | Σ ventas recibidas |
| ledger_out | bigint default 0 | Σ `sent_total` de sus patas. Saldo = `ledger_in − ledger_out` |
| revoked_at | timestamptz null | |

### `credit_assessments` · dueño: 09-scoring (sin `updated_at`; inmutable)
| Columna | Tipo | Notas |
|---|---|---|
| merchant_id | fk users | índice (merchant_id, computed_at desc) |
| policy_version | text | p. ej. `v1` |
| score | int | 0–1000 |
| grade | `grade` | |
| eligible | bool | |
| unlocks_at | timestamptz null | si no es elegible: fecha estimada de desbloqueo |
| cupo_total | bigint | + asset_code, asset_scale |
| features | jsonb | insumos usados |
| reasons | jsonb | lista de textos cortos para mostrar |
| computed_at | timestamptz | |

### `loan_requests` · dueño: 10-marketplace
`merchant_id` fk · `min_amount`, `max_amount` bigint (`CHECK 0 < min ≤ max`) + asset · `pct_bps` int · `status request_status` · `credit_assessment_id` fk. Índice único parcial: una `open` por negocio.

### `loan_offers` · dueño: 10-marketplace
`lender_id` fk · `min_amount`, `max_amount` bigint (`CHECK 0 < min ≤ max`) + asset · `pct_bps` int · `fee_bps` int (`≥ 0`) · `term_months` int (`≥ 1`) · `status offer_status`.

### `loans` · dueño: 11-loans
| Grupo | Columnas |
|---|---|
| Partes | `merchant_id`, `lender_id` fk · `request_id`, `offer_id` fk null · `origin loan_origin` · `created_by` fk users · `credit_assessment_id` fk |
| Congelado al crear | `collection_account_id` fk · `merchant_payout_wallet_url` · `lender_wallet_url` · `lender_asset_code`, `lender_asset_scale` · `asset_code`, `asset_scale` (asset del crédito) |
| Términos (inmutables) | `principal`, `fee_bps`, `fee_amount`, `total_repayable`, `trust_share_bps`, `term_months`, `monthly_minimum` · `pct_initial_bps`, `pct_min_bps`, `pct_max_bps` · `terms jsonb` (snapshot de lo mostrado en el preview) |
| Estado | `status loan_status` · `pct_bps` (vigente) · `is_late bool default false` · `missed_periods int default 0` · `period_start`, `next_review_at` timestamptz null |
| Contabilidad | `allocated_total`, `delivered_total`, `trust_earned`, `trust_pending` bigint default 0 (`CHECK allocated_total ≤ total_repayable`) |
| Desembolso | `op_disbursement_payment_id` uuid null · `disbursement_attempt int default 0` · `disbursement_authorization_url` text null · `disbursement_authorization_expires_at` timestamptz null |
| Tiempos | `expires_at` (estados pendientes) · `accepted_at`, `disbursement_started_at`, `activated_at`, `paid_at`, `cancelled_at`, `rejected_at`, `expired_at` (todas null) |

Índices únicos parciales: `merchant_id` donde `status` es no terminal · `offer_id` donde no es null y `status` no es `cancelled`/`rejected`/`expired` · igual para `request_id`.

### `loan_pct_changes` · dueño: 11-loans (sin `updated_at`)
`loan_id` fk · `old_pct_bps`, `new_pct_bps` · `reason pct_change_reason` · `period_start`, `period_end` · `allocated_in_period`, `target_amount` bigint. Se escribe una fila por **cada** revisión de periodo (también si no cambió: `unchanged`).

### `sales` · dueño: 07-sales (sin `updated_at`)
`merchant_id`, `collection_account_id` fk · `puntored_event_id` text único null (null en seed) · `bre_b_reference` text · `amount` + asset · `payer_bank` text null · `incoming_payment_url` text null · `paid_at` timestamptz · `source sale_source` · `status sale_status`. Único: (`collection_account_id`, `bre_b_reference`). Índice: (`merchant_id`, `paid_at`).

### `repayments` · dueño: 07-sales (sin `updated_at`; lo lee 12-repayments)
`sale_id` fk **único** · `loan_id`, `merchant_id`, `lender_id` fk · `abono_amount`, `trust_cut_amount`, `lender_net_amount` bigint + asset · `pct_applied_bps` · `allocated_before`, `allocated_after` bigint. Índices: (`loan_id`, `created_at`), (`lender_id`, `created_at`).

### `payouts` · dueño: 07-sales (los crea) y 08-worker (los ejecuta)
| Columna | Tipo | Notas |
|---|---|---|
| sale_id | fk sales | único (`sale_id`, `leg`) |
| loan_id | fk null | null en la pata `merchant` sin crédito |
| leg | `payout_leg` | |
| receiver_wallet_url | text null | congelado al crear; `null` solo en la pata `merchant` `held` porque el negocio no tiene payout wallet (07 R4), que 16 llena al reintentar |
| amount | bigint + asset | monto total de la pata |
| description | text | congelada al crear (el reintento manda el mismo cuerpo) |
| status | `payout_status` | |
| attempt | int default 1 | sube con `failed` + `retryable` (08) y con el reintento manual del admin (16) |
| current_debit_amount | bigint | monto del intento actual = `amount − sent_total` al crear el intento |
| sent_total | bigint default 0 | lo efectivamente entregado |
| op_transfer_id | uuid null | id de la transferencia del intento actual |
| receive_value, receive_asset_code, receive_asset_scale | null | lo que recibió el receptor (puede ser otro asset) |
| failure_code, last_error | text null | |
| next_attempt_at | timestamptz null | backoff |
| incoming_payment_url, outgoing_payment_url | text null | trazabilidad |
| completed_at | timestamptz null | |
Índice: (`status`, `next_attempt_at`).

### `webhook_events` · dueño: 07-sales (sin `updated_at`)
`event_id` text único · `source` (`puntored` \| `reconciliation`) · `type` text · `payload jsonb` · `status webhook_status` null (se llena al terminar de procesar, en la misma transacción) · `sale_id` fk null · `error` text null · `received_at`, `processed_at` timestamptz.

### `op_cursors` · dueño: 08-worker
`name text pk` · `value bigint` · `updated_at`. Sin `id` ni `created_at`.

### `notifications` · dueño: 14-notifications (sin `updated_at`)
`user_id` fk · `type` text (catálogo en 00-core) · `title`, `body` text · `data jsonb` · `read_at` timestamptz null. Índice: (`user_id`, `created_at desc`).

### `waitlist` · dueño: 15-waitlist
`tipo waitlist_tipo` · `nombre` · `correo` text único (minúsculas) · `ciudad`.

### `events` · dueño: 00-core (sin `updated_at`)
`entity_type` text · `entity_id` uuid · `type` text · `actor_id` fk null · `data jsonb`. Índice: (`entity_type`, `entity_id`, `created_at`).

## Criterios de aceptación
- [ ] **AC-1.** `create_all` crea todas las tablas con sus `CHECK`, índices y únicos parciales en Postgres (→ `test_ac1_schema_creates`).
- [ ] **AC-2.** Un segundo crédito no terminado para el mismo negocio falla por el índice único parcial (→ `test_ac2_one_open_loan_per_merchant`).
- [ ] **AC-3.** Una segunda solicitud `open` del mismo negocio falla; una `cancelled` más una `open` sí se permite (→ `test_ac3_one_open_request`).
- [ ] **AC-4.** Insertar dos `payouts` con el mismo (`sale_id`, `leg`) falla (→ `test_ac4_payout_unique_leg`).
