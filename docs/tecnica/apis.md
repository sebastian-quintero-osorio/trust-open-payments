# APIs e integración de Trust

Trust expone una API de negocio bajo `/api/v1`; Go expone operaciones financieras internas bajo `/v1`, más páginas de consentimiento y QR. El frontend usa la primera. La API key de Go y las credenciales de wallets permanecen en los servicios.

## Contratos y copias

| Archivo | Consumidor |
| --- | --- |
| [contracts/trust-api.openapi.yaml](../../contracts/trust-api.openapi.yaml) | Contrato compartido y validadores de pruebas Python |
| [trust-api.openapi.yaml](../../trust-api.openapi.yaml) | Copia utilizada por el generador frontend |
| [contracts/op-service.openapi.yaml](../../contracts/op-service.openapi.yaml) | Contrato compartido Python–Go y pruebas del cliente/mock |
| [open-payments/op-service.openapi.yaml](../../open-payments/op-service.openapi.yaml) | Contrato embebido y generación de modelos Go |

Ambas APIs declaran OpenAPI 3.1.0 y versión 0.1.0. Las copias de Trust son idénticas en esta revisión. Las de Go tienen las mismas rutas y schemas; difieren en una línea descriptiva sobre el sufijo de la clave de reasignación de recaudo. No hay una diferencia estructural de endpoints en ese cambio, pero las copias deben mantenerse sincronizadas.

La API de negocio contiene 43 rutas y 48 operaciones HTTP; Go contiene 14 rutas y 17 operaciones. El webhook documentado en la sección `webhooks` del contrato Go se recibe en Python y no se cuenta como una ruta servida por Go.

Swagger de FastAPI está en `/docs`, ReDoc en `/redoc` y su esquema generado en `/openapi.json`. Los contratos compartidos contienen además descripciones, convenciones y ejemplos mantenidos por el proyecto; no deben sustituirse automáticamente por el esquema generado de FastAPI.

## Convenciones de cliente

Peticiones y respuestas JSON usan `snake_case`, IDs UUID y fechas ISO 8601 UTC. Los filtros `from` y `to` se interpretan como fechas locales de `TIMEZONE`. Las listas paginadas usan `limit` entre 1 y 200, por defecto 50, y `offset`; la respuesta contiene `items` y `next_offset`. Consultar el schema de cada operación: algunas listas cortas tienen otra forma.

```json
{"value":"4800000","asset_code":"COP","asset_scale":2}
```

Ese objeto representa $48.000 COP. `value` es texto entero sin formato; no debe convertirse en un float para calcular importes. Los campos `*_bps` representan puntos básicos.

Los errores de Trust tienen esta envoltura:

```json
{"error":{"code":"unauthorized","message":"Inicia sesión para continuar.","details":{}}}
```

La UI decide por `code`, no por el texto de `message`. Sin token válido se recibe 401; rol o acceso incorrecto puede producir 403; recurso inexistente, 404; conflictos de estado o reserva, 409; validación, 422. Los errores de la integración pueden propagarse como 502/503. Revisar los códigos definidos para cada operación antes de programar reintentos.

Los errores de Go utilizan `{"error":{"code":"…","message":"…","retryable":false}}`; no comparten el campo `details` de Trust. Un POST de pago aceptado puede fallar después: su resultado financiero se consulta con GET.

## Autenticación y autorización

`POST /api/v1/auth/login` y el registro retornan `access_token`. Las operaciones protegidas reciben `Authorization: Bearer <token>`. Se firman con HS256 y claims `sub`, `role`, `exp`; la duración predeterminada es 168 horas. La dependencia consulta al usuario en base de datos y aplica su rol actual y las comprobaciones de pertenencia de cada servicio.

Las contraseñas usan bcrypt con coste 12 en ejecución normal. El contrato no ofrece refresh token, recuperación de contraseña, MFA ni revocación individual de JWT. Cerrar sesión en el frontend elimina su copia local del token.

El rol `admin` tiene sus rutas operativas específicas; no equivale a permiso genérico para realizar todas las acciones de comercio y prestamista. El prestamista solo puede consultar el perfil crediticio de un comercio con solicitud abierta o con un crédito relacionado con él; esa vista expone una franja de ventas, no el detalle de ventas de terceros.

Las rutas de demo no requieren JWT cuando están habilitadas. `DEV_ENDPOINTS_ENABLED=false` las bloquea con 404 antes de procesar el cuerpo. Deben mantenerse apagadas en el despliegue operativo.

Go exige `X-Internal-Api-Key` en rutas privadas y `Idempotency-Key` en sus POST privados. Health, callback y páginas QR tienen el tratamiento público declarado en su contrato. No colocar esas claves en `frontend/src/config.js` ni en variables `PUBLIC_*` de la landing.

Fuentes: [seguridad](../../backend/app/core/security.py), [dependencias](../../backend/app/core/deps.py), [servidor Go](../../open-payments/internal/httpapi/server.go).

El transporte saliente de Go exige HTTPS, valida los destinos al conectar, bloquea direcciones privadas/especiales y no sigue redirecciones ni usa proxies del entorno. Los callbacks comprueban el estado de interacción; las URLs de retorno se restringen por origen y prefijo. El servicio conserva los tokens de grants cifrados y no los entrega a Trust ni al frontend. Estas medidas están implementadas en [network.go](../../open-payments/internal/openpayments/network.go), [callback.go](../../open-payments/internal/workflow/callback.go) y [vault.go](../../open-payments/internal/workflow/vault.go).

## Webhook de ventas

Destino: `POST /api/v1/webhooks/puntored/payments`. No utiliza JWT; autentica mediante `X-Puntored-Signature`:

```text
t=<timestamp-unix>,v1=<digest-hexadecimal>
digest = HMAC-SHA256(secreto, bytes(t) + b"." + cuerpo_original)
```

Se compara la firma en tiempo constante contra los secretos configurados y se admite una diferencia de reloj de hasta 300 segundos. Una firma válida pero vieja produce `signature_expired`. Al reintentar, el emisor firma de nuevo el mismo evento con timestamp reciente.

El evento `qr_payment.received` incluye `event_id`, `type`, `occurred_at` y `data`: ID/dirección de recaudo, monto, referencia Bre-B y datos de trazabilidad según el schema. La API deduplica por ID de evento y por referencia dentro de la cuenta. El cuerpo admite como máximo 64 KiB; el límite general de Trust es 1 MiB y el de waitlist también 64 KiB.

Una recepción puede terminar como `processed`, `duplicate`, `unmatched` o `ignored`. Confirmar recepción no significa que las transferencias de reparto ya se hayan completado. Los eventos no reconocidos no disparan reversos financieros.

Fuentes: [router de ventas](../../backend/app/modules/sales/router.py), [verificación y procesamiento](../../backend/app/modules/sales/service.py), [outbox emisor](../../open-payments/internal/application/webhook.go), [límites de cuerpo](../../backend/app/core/limits.py).

## Secuencia mínima para integrar un cliente

1. Consultar `/config`; registrar o autenticar al usuario y obtener `/me`.
2. Vincular la wallet con `PUT /me/payout-wallet`; para el comercio, crear/consultar `/me/collection-account` y consultar `/me/credit-profile`.
3. Publicar una solicitud u oferta y consultar el mercado con el `scope` adecuado. Usar `/matches` cuando se cruzan publicaciones existentes.
4. Enviar el borrador a `/loans/preview`; mostrar términos; crear con `/loans`. Reaccionar a los conflictos si el estado cambió desde el preview.
5. Si se requiere, el comercio acepta. El prestamista inicia `/loans/{id}/disburse` con `{"client":"web"}` y navega a `authorization_url`.
6. Al retornar, consultar `/loans/{id}` hasta confirmar el estado. No aceptar `op_status` de la URL como prueba del pago.
7. Consultar ventas, abonos y notificaciones. Para ventas nuevas, pasar a `/sales/today` el `since` recibido como `next_since`.

Las rutas de esa secuencia, salvo `/config`, están abreviadas respecto al prefijo `/api/v1`. Los schemas completos de entrada están en el YAML; por ejemplo, el borrador puede incluir `offer_id`, `request_id`, `amount` y, al fondear directamente una solicitud, cargo y plazo.

## Catálogo de Trust API

La columna acceso identifica el mecanismo de autenticación. Las restricciones adicionales de rol, propiedad y estado están en el contrato y los servicios; “JWT” no significa acceso permitido para cualquier usuario autenticado.

| Método | Ruta | Operación | Acceso |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/register` | `register` | Público |
| `POST` | `/api/v1/auth/login` | `login` | Público |
| `GET` | `/api/v1/me` | `getMe` | JWT |
| `PATCH` | `/api/v1/me` | `updateMe` | JWT |
| `PUT` | `/api/v1/me/payout-wallet` | `setPayoutWallet` | JWT |
| `GET` | `/api/v1/me/collection-account` | `getCollectionAccount` | JWT |
| `POST` | `/api/v1/me/collection-account` | `createCollectionAccount` | JWT |
| `GET` | `/api/v1/me/credit-profile` | `getMyCreditProfile` | JWT |
| `GET` | `/api/v1/merchants/{merchant_id}/credit-profile` | `getMerchantCreditProfile` | JWT |
| `POST` | `/api/v1/loan-requests` | `createLoanRequest` | JWT |
| `GET` | `/api/v1/loan-requests` | `listLoanRequests` | JWT |
| `GET` | `/api/v1/loan-requests/{request_id}` | `getLoanRequest` | JWT |
| `POST` | `/api/v1/loan-requests/{request_id}/cancel` | `cancelLoanRequest` | JWT |
| `POST` | `/api/v1/loan-offers` | `createLoanOffer` | JWT |
| `GET` | `/api/v1/loan-offers` | `listLoanOffers` | JWT |
| `GET` | `/api/v1/loan-offers/{offer_id}` | `getLoanOffer` | JWT |
| `POST` | `/api/v1/loan-offers/{offer_id}/cancel` | `cancelLoanOffer` | JWT |
| `GET` | `/api/v1/matches` | `listMatches` | JWT |
| `POST` | `/api/v1/loans/preview` | `previewLoan` | JWT |
| `POST` | `/api/v1/loans` | `createLoan` | JWT |
| `GET` | `/api/v1/loans` | `listLoans` | JWT |
| `GET` | `/api/v1/loans/{loan_id}` | `getLoan` | JWT |
| `POST` | `/api/v1/loans/{loan_id}/accept` | `acceptLoan` | JWT |
| `POST` | `/api/v1/loans/{loan_id}/reject` | `rejectLoan` | JWT |
| `POST` | `/api/v1/loans/{loan_id}/cancel` | `cancelLoan` | JWT |
| `POST` | `/api/v1/loans/{loan_id}/disburse` | `disburseLoan` | JWT |
| `GET` | `/api/v1/loans/{loan_id}/pct-history` | `getLoanPctHistory` | JWT |
| `GET` | `/api/v1/sales/today` | `getSalesToday` | JWT |
| `GET` | `/api/v1/sales` | `listSales` | JWT |
| `GET` | `/api/v1/sales/{sale_id}` | `getSale` | JWT |
| `POST` | `/api/v1/webhooks/puntored/payments` | `puntoredPaymentWebhook` | HMAC |
| `GET` | `/api/v1/repayments` | `listRepayments` | JWT |
| `GET` | `/api/v1/repayments/{repayment_id}` | `getRepaymentTrace` | JWT |
| `GET` | `/api/v1/dashboard/merchant` | `getMerchantDashboard` | JWT |
| `GET` | `/api/v1/dashboard/lender` | `getLenderDashboard` | JWT |
| `GET` | `/api/v1/notifications` | `listNotifications` | JWT |
| `POST` | `/api/v1/notifications/read-all` | `readAllNotifications` | JWT |
| `POST` | `/api/v1/notifications/{notification_id}/read` | `readNotification` | JWT |
| `POST` | `/api/v1/waitlist` | `joinWaitlist` | Público |
| `GET` | `/api/v1/admin/dashboard` | `getAdminDashboard` | JWT admin |
| `POST` | `/api/v1/admin/payouts/{payout_id}/retry` | `retryPayout` | JWT admin |
| `POST` | `/api/v1/admin/collection-accounts/{collection_account_id}/revoke` | `revokeCollectionAccount` | JWT admin |
| `GET` | `/api/v1/health` | `getHealth` | Público |
| `GET` | `/api/v1/config` | `getConfig` | Público |
| `POST` | `/api/v1/dev/seed` | `devSeed` | Demo habilitada |
| `POST` | `/api/v1/dev/sales/simulate` | `devSimulateSale` | Demo habilitada |
| `POST` | `/api/v1/dev/loans/{loan_id}/close-period` | `devClosePeriod` | Demo habilitada |
| `GET` | `/api/v1/dev/fake-wallet/authorize/{payment_id}` | `devFakeWalletAuthorize` | Demo habilitada |

## Catálogo de op-service

Las operaciones privadas se invocan desde servicios u operadores autorizados. Los POST privados requieren además idempotencia. Las rutas QR corresponden al simulador.

| Método | Ruta | Operación | Acceso |
| --- | --- | --- | --- |
| `GET` | `/v1/health` | `getHealth` | Público |
| `GET` | `/v1/wallet-addresses` | `resolveWalletAddress` | API key |
| `GET` | `/v1/collection-accounts` | `listCollectionAccounts` | API key |
| `POST` | `/v1/collection-accounts` | `createCollectionAccount` | API key |
| `GET` | `/v1/collection-accounts/{collection_account_id}` | `getCollectionAccount` | API key |
| `DELETE` | `/v1/collection-accounts/{collection_account_id}` | `revokeCollectionAccount` | API key |
| `POST` | `/v1/transfers` | `createTransfer` | API key |
| `GET` | `/v1/transfers/{transfer_id}` | `getTransfer` | API key |
| `POST` | `/v1/payments` | `createPayment` | API key |
| `GET` | `/v1/payments/{payment_id}` | `getPayment` | API key |
| `POST` | `/v1/sim/puntored/qr-payments` | `createSimQrPayment` | API key |
| `GET` | `/v1/sim/puntored/qr-payments` | `listSimQrPayments` | API key |
| `GET` | `/v1/admin/pool-wallets` | `listPoolWallets` | API key |
| `POST` | `/v1/admin/pool-wallets/{pool_wallet_id}/authorize` | `authorizePoolWallet` | API key |
| `GET` | `/v1/interact/finish/{attempt_id}` | `interactFinish` | Callback público validado |
| `GET` | `/qr/{collection_account_id}` | `qrPage` | Público |
| `POST` | `/qr/{collection_account_id}/pay` | `qrPagePay` | Público |

## Actualización de contratos

Al cambiar una API, actualizar el contrato compartido y sus copias consumidoras. Desde `frontend/`, `python3 scripts/contract.py` regenera `src/lib/operations.js` y `tests/openapi.json` y requiere PyYAML. Desde `open-payments/`, `go generate ./...` regenera los modelos Go según sus directivas. Revisar los diffs y ejecutar las suites de contrato correspondientes.

La [verificación](verificacion-y-pendientes.md) distingue el inventario y los ejemplos comprobados de las pruebas que requieren infraestructura.
