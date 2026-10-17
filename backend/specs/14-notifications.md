# 14 · Notificaciones in-app

Estado: ☐ pendiente · Fase: 3 · Dueño: — · Carpeta: `app/modules/notifications/`

## Objetivo
Avisos dentro de la app (web y móvil, por polling) para comercio, prestamista y admin, con un catálogo único de tipos y textos. Sin push ni correo.

## Endpoints
| ★ | Método y ruta | Rol | Request | Response | Errores |
|---|---|---|---|---|---|
| | `GET /api/v1/notifications` | merchant, lender, admin | Paginación | `{"unread_count": int, "items": [Notificación], "next_offset"}` | 422 `validation_error` (limit/offset fuera de rango) |
| | `POST /api/v1/notifications/read-all` | merchant, lender, admin | — | `{"unread_count": 0, "updated": int}` | — |
| | `POST /api/v1/notifications/{notification_id}/read` | dueño | — | `Notificación` | 403 `forbidden`, 404 `not_found` |

`Notificación`:
```json
{"id": "…", "type": "abono_received", "title": "Abono recibido · +$7,20 · Tienda La Esquina",
 "body": "Venta de $48,00 a las 10:42 a. m. Parte Trust $0,13, neto $7,07.",
 "data": {"loan_id": "…", "repayment_id": "…", "sale_id": "…", "payout_id": "…",
          "gross": {"value": "720", "asset_code": "USD", "asset_scale": 2}, "trust_cut": {"value": "13", "asset_code": "USD", "asset_scale": 2},
          "net": {"value": "707", "asset_code": "USD", "asset_scale": 2}, "received": {"value": "650", "asset_code": "EUR", "asset_scale": 2}},
 "read_at": null, "created_at": "2026-10-16T15:42:09Z"}
```

## Reglas

### Catálogo (los textos de esta tabla mandan; la spec emisora solo decide cuándo)
Variables: `{negocio}` = business_name · `{prestamista}` = LenderRef.public_name · montos con `format_es` · `{pct}` como `15 %` · `{hora}` con `format_hora`. Si hay dos textos, el primero es para el comercio y el segundo para el prestamista. `data` lleva siempre `loan_id` cuando hay crédito, más lo indicado.

| type | Recibe | Emite | Cuándo | Título | Cuerpo | data extra |
|---|---|---|---|---|---|---|
| `offer_received` | merchant | 11 | Un prestamista crea un crédito sobre tu solicitud (`pending_acceptance`) | Recibiste una oferta | {prestamista} te ofrece {monto} con el {pct} de cada venta. Revísala antes de aceptar. | `principal`, `pct_bps` |
| `loan_accepted` | lender | 11 | El negocio toma tu oferta o acepta tu crédito (`pending_disbursement`) | {negocio} aceptó tu oferta | Autoriza en tu wallet el desembolso de {monto}. | `principal` |
| `action_required` | lender | 11 (`on_disbursement_update`) | El desembolso vuelve a `pending_disbursement` (pago `failed`/`rejected`/`expired`) | El desembolso no se completó | Vuelve a autorizar el desembolso de {monto} a {negocio}. | `failure_code` |
| `loan_active` | merchant y lender | 11 (`on_disbursement_update`) | Desembolso `completed` → `active` | Tu crédito está activo / Desembolso completado | Recibiste {monto}. Desde hoy, el {pct} de cada venta abona a tu crédito. / {negocio} recibió {monto}. Los abonos llegan con cada venta. | `principal`, `pct_bps` |
| `loan_rejected` | la otra parte | 11 | Quien debía aceptar o desembolsar rechaza | Crédito rechazado | {quien} rechazó el crédito de {monto}. | `principal` |
| `loan_cancelled` | la otra parte | 11 | Una parte cancela antes del desembolso (`pending_*` → `cancelled`) | Crédito cancelado | {quien} canceló el crédito de {monto}. Tu solicitud u oferta vuelve a estar abierta. | `principal` |
| `loan_expired` | merchant y lender | 11 (`expire_pending`) | Pasa `expires_at` sin desembolso | El crédito venció | Pasaron {horas} h sin desembolso. Tu solicitud u oferta vuelve a estar abierta. | — |
| `loan_paid` | merchant y lender | 07 | `allocated_total == total_repayable` | ¡Pagaste tu crédito! / Crédito pagado | Completaste {total} con tus ventas. / {negocio} completó el pago de {total}. | `total_repayable` |
| `loan_late` | merchant y lender | 11 (`close_period`) | `missed_periods` llega a 2 | Tu crédito está atrasado / Crédito atrasado | No alcanzaste el mínimo en {n} periodos. / {negocio} no alcanzó el mínimo en {n} periodos. | `missed_periods` |
| `pct_changed` | merchant y lender | 11 (`close_period`) | `new_pct_bps != old_pct_bps` | Cambió tu porcentaje / {negocio} cambió de porcentaje | Desde ahora abonas el {pct_nuevo} de cada venta (antes {pct_anterior}). / Ahora abona el {pct_nuevo} de cada venta (antes {pct_anterior}). | `old_pct_bps`, `new_pct_bps`, `reason` |
| `abono_received` | lender | 08 | Pata `lender` `completed` | Abono recibido · +{bruto} · {negocio} | Venta de {venta} a las {hora}. Parte Trust {trust}, neto {neto}. | `repayment_id`, `sale_id`, `payout_id`, `gross`, `trust_cut`, `net`, `received` |
| `sale_split` | merchant | 07 (si `NOTIFY_SALE_SPLIT`) | Venta `processed` | Venta de {venta} · {para_ti} para ti | {abono} abonado a tu crédito. (Sin crédito: El 100 % va a tu wallet.) | `sale_id`, `amount`, `merchant_amount`, `abono` |
| `payout_failed` | cada admin | 08 | Pata `failed_final` | Transferencia fallida | Pata {leg} de {monto} · {failure_code}. La plata queda en recaudo. | `payout_id`, `sale_id`, `leg`, `failure_code` |

### Comportamiento
- **R1. `notify(db, user_id, type, title, body, data)`** inserta en la transacción de quien llama (no hace commit). `type` fuera del catálogo → `ValueError`.
- **R2. Textos.** `catalog.render(type, audience, **vars) -> (title, body)` arma título y cuerpo desde la tabla (`audience` = `merchant` | `lender` | `admin`). Los emisores llaman `notify(db, user_id, type, *render(...), data)`. Montos con `money.format_es` (`$48.000`; con `asset_scale > 0`, `$480,00`) y porcentaje con espacio (`15 %`, `12,5 %`). `data` lleva ids y Money crudos para enlazar y re-formatear en la app.
- **R3. Copy.** Tuteo; nunca "goteo", "gotereado" ni "gota a gota" para Trust; sin tasas ni rendimientos (COPY §0).
- **R4. Admins.** "Cada admin" = un `notify` por usuario con `role = admin`.
- **R5. Lista.** Solo las del usuario, orden `created_at` desc. `unread_count` = total del usuario con `read_at` null (no depende de la página).
- **R6. Leer.** `read-all` pone `read_at = now` en las del usuario sin leer y devuelve cuántas cambió; idempotente. `/{id}/read`: solo el dueño (ajena → 403); si ya estaba leída, no cambia `read_at`.
- **R7.** Solo in-app: la app consulta `GET /notifications` al abrir y cada ~30 s.

## Datos
- Lee: `notifications`, `users` (admins) · Escribe: `notifications`.

## Depende de
- `00-core` (`clock`, `money`, paginación) · `04-users` (auth y roles). Emisores: 07, 08, 11.

## Criterios de aceptación
- [ ] **AC-1.** Dadas 3 notificaciones propias (1 leída) y 2 de otro usuario, cuando listo, entonces 3 items en orden desc y `unread_count` 2; con `limit=2`, `next_offset` 2 (→ `test_ac1_list_own`)
- [ ] **AC-2.** `read-all` deja `unread_count` 0 y `updated` 2; las del otro usuario siguen sin leer; una segunda llamada da `updated` 0 (→ `test_ac2_read_all`)
- [ ] **AC-3.** Leer una propia pone `read_at`; repetir no lo cambia; una ajena → 403; una inexistente → 404 (→ `test_ac3_read_one`)
- [ ] **AC-4.** `render("abono_received", "lender", bruto=Money(720, "USD", 2), negocio="Tienda La Esquina", …)` da el título `Abono recibido · +$7,20 · Tienda La Esquina`; `format_es(Money(48000, "COP", 0))` da `$48.000` y `format_es(Money(48000, "USD", 2))` da `$480,00` (→ `test_ac4_catalog_render`)
- [ ] **AC-5.** Un `notify` dentro de una transacción que hace rollback no deja fila; un `type` fuera del catálogo lanza `ValueError` (→ `test_ac5_notify_transactional`)
- [ ] **AC-6.** Cada uno de los 13 `type` de 00-core tiene textos en `catalog` para cada `audience` que lo recibe (→ `test_ac6_catalog_complete`)

## Fuera de alcance
- Push, correo, SMS, WebSockets · preferencias por tipo · borrar notificaciones.
