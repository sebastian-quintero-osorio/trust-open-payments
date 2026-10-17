# op-service

Servicio Go interno de Trust. El contrato original es `op-service.openapi.yaml`.
Incluye servidor, configuración, modelos, validación, persistencia, idempotencia,
cliente Open Payments, consentimiento, workers financieros, simulador QR y validaciones locales.
Las validaciones locales usan HTTPS y PostgreSQL; la aceptación real con Test Wallet
queda para el cierre con las credenciales configuradas por el operador.

## Entorno

Requiere Go 1.26.9, GCC para el detector de carreras, PostgreSQL 17 y Python 3.
Los secretos se inyectan por el entorno; no deben guardarse en el repositorio.
Variables obligatorias al arrancar:

| Variable | Formato |
| --- | --- |
| `INTERNAL_API_KEY` | Secreto aleatorio de al menos 32 bytes |
| `DATABASE_URL` | URL PostgreSQL; usar TLS fuera del entorno local aislado |
| `PUBLIC_BASE_URL` | URL HTTPS pública de callbacks y QR |
| `OP_CLIENT_WALLET_ADDRESS` | Wallet HTTPS del cliente Trust |
| `OP_KEY_ID` | Identificador de su clave pública registrada |
| `OP_PRIVATE_KEY` | PEM PKCS#8 Ed25519, incluidos saltos de línea |
| `ALLOWED_RETURN_URL_PREFIXES` | Prefijos HTTPS separados por comas; opcional en modo demo |

También se admiten `POOL_WALLET_ADDRESSES`, `SIM_PAYER_WALLET_ADDRESS`,
`SIM_ENABLED` (por defecto false), `SIM_MAX_AMOUNT`, `TRUST_WEBHOOK_URL`,
`TRUST_WEBHOOK_SECRET`, `INTERACTION_TIMEOUT_SECONDS` (600) y
`POLL_INTERVAL_SECONDS` (2). Activar el simulador exige una wallet pagadora,
un monto máximo entero positivo, webhook HTTPS y secreto de al menos 32 bytes.
La wallet pagadora no puede estar en el pool de recaudo.

Con las variables configuradas, ejecutar `go run ./cmd/op-service`. El servidor
escucha en `:8080`, aplica migraciones verificadas mediante checksum y atiende
`GET /v1/health`. Las señales SIGINT/SIGTERM permiten hasta 10 segundos de cierre.
Health devuelve `degraded` si falla la conexión a PostgreSQL.

## Contrato y seguridad

`go generate ./...` regenera modelos a partir del contrato intacto. Los cuerpos se
validan con JSON Schema 2020-12, incluyendo UUID, límites, montos positivos y null.
Los montos son strings enteros de precisión arbitraria; nunca floats.
Las rutas privadas exigen `X-Internal-Api-Key` antes de procesar el cuerpo.
Callbacks y QR tienen acceso público según el contrato. Cada POST privado exige
`Idempotency-Key`; los cuerpos están limitados a 64 KiB y 64 niveles de anidación.
Se rechazan claves JSON duplicadas y documentos adicionales.

La idempotencia usa locks transaccionales no bloqueantes por método/ruta/clave.
La creación del recurso y su respuesta se guardan en una misma transacción.
Se conservan 72 horas los 2xx y errores definitivos 400/404/409 de estado/422;
los 401, 5xx y `request_in_progress` no se guardan. Los errores tampoco confirman
mutaciones del recurso. Los replays conservan status, bytes y headers seguros.
Un callback de repositorio **solo puede efectuar cambios locales**: los efectos
financieros externos deberán usar trabajos durables en las fases posteriores.

## Validaciones

Usar una base de datos exclusiva para pruebas. Instalar los analizadores y el
validador, añadir `$(go env GOPATH)/bin` al PATH y ejecutar:

```bash
./scripts/install-tools.sh
python3 -m venv /tmp/op-service-validation
source /tmp/op-service-validation/bin/activate
python3 -m pip install -r scripts/requirements-tools.txt
export TEST_DATABASE_URL='postgres://usuario@localhost/op_service_test'
./scripts/check.sh
```

El script exige PostgreSQL para que las pruebas de integración no se omitan.
Incluye formato, regeneración, módulos, vet, Staticcheck, race tests, gosec,
govulncheck, OpenAPI y Gitleaks sobre archivos e historial. El workflow de GitHub
Actions se retiró temporalmente por solicitud del usuario; las validaciones se
ejecutan localmente. No se ha configurado protección remota ni ejecutado Actions.

## Integración pendiente

Las pruebas reales de Open Payments requieren una wallet Trust, clave Ed25519
registrada y wallets de usuarios separados para recaudo, ingresos y pagador demo.
El backend FastAPI está en `../backend/` e incluye cliente HTTP, receptor de webhooks
y workers para desembolsos, repartición y conciliación. Por defecto usa `OP_CLIENT=fake`;
la conexión con este servicio requiere `OP_CLIENT=http`. Ver la configuración de
dominio y red interna en [la guía operativa](docs/operations.md).
Las pruebas locales no certifican el comportamiento de la wallet real.

Referencias: [Open Payments](https://openpayments.dev/),
[pgx](https://github.com/jackc/pgx),
[JSON Schema validator](https://github.com/santhosh-tekuri/jsonschema).


## Cliente Open Payments (fase 2)

Se usa el SDK oficial `interledger/open-payments-go` v0.5.0 para firmar peticiones.
Los adaptadores usan `DoSigned` y tipos de montos propios para conservar precisión
arbitraria y las URLs opacas de continuación/rotación sin las restricciones de ruta
de algunos métodos de alto nivel del SDK. El transporte exige HTTPS, bloquea IPs
privadas/especiales, valida DNS al conectar y no sigue redirects ni usa proxies del
entorno. Cada respuesta tiene un límite de 1 MiB y cada llamada tiene timeout.
Los POST financieros no se reintentan automáticamente.

Pruebas locales de protocolo verifican las firmas con la clave pública, además de
los recursos y errores. La validación con Test Wallet se difiere al final por
instrucción del usuario: sus credenciales no son requisito para desarrollar el resto.
Referencias consultadas: [grants](https://openpayments.dev/identity/grants/),
[firmas](https://openpayments.dev/identity/http-signatures/),
[SDK Go](https://github.com/interledger/open-payments-go).


## Autorización y recuperación (fase 3)

Los grants se guardan cifrados mediante AES-256-GCM. HKDF deriva una clave separada
con contexto `trust/op-service/vault/v1` desde la semilla Ed25519 configurada: no
se requieren secretos adicionales. El ID del registro se autentica como AAD.
**Cambiar OP_PRIVATE_KEY requiere migrar los ciphertexts con la clave anterior**;
conservar esa clave de forma segura junto con los backups.

La rotación se serializa mediante advisory locks de PostgreSQL y guarda primero
un marcador durable. Si se pierde la respuesta, el grant queda bloqueado para
nuevas salidas y requiere reautorización; nunca se vuelve a rotar a ciegas. Token
y management URL se reemplazan juntos. Los callbacks verifican hash/nonce antes
de consumir el intento, y su consumo comparte transacción con el trabajo pendiente.
Las colas usan leases, dueños y SKIP LOCKED; un worker antiguo no puede confirmar
un lease reasignado. Los estados financieros terminales no admiten transiciones.


## Flujos de Trust (fase 4)

El arranque resuelve y registra el pool sin reiniciar autorizaciones existentes.
El operador usa `/v1/admin/pool-wallets/{id}/authorize` y aprueba cada wallet.
Las cuentas se asignan con SKIP LOCKED y exclusión por referencia activa. El pagador
demo nunca se asigna a un comercio. Los desembolsos preparan incoming, quote y grant
interactivo; el callback durable continúa el grant y el worker ejecuta la salida.
Las transferencias fixed-send crean incoming sin importe y llaman a Complete antes
de confirmar éxito. El simulador usa fixed-receive, quote y el grant del pagador.

Cada salida incluye metadata.external_ref y un op_operation_id único. El checkpoint
previo al POST evita repetir salidas tras un reinicio o una respuesta perdida. En
ese caso se busca el outgoing por metadata y se mantiene processing mientras su
resultado siga incierto. **La ausencia en un listado no autoriza un nuevo envío**.
Si no puede encontrarse, se necesita conciliación del operador, no un reintento de
pago. La aplicación nunca modifica montos ni reenvía automáticamente dinero.

El QR usa formularios de un solo uso, límite por IP de conexión, máximo configurable
y conversiones enteras de unidades mayores. El webhook se guarda una vez, se firma
con HMAC-SHA256 en cada envío y se reintenta a 1 s, 5 s, 30 s y 2 min. La secuencia
de conciliación se asigna al alcanzar estado terminal, sin saltarse pagos que
terminen fuera de orden. Un sim completado ya tuvo al menos un intento de webhook.
El saldo de recaudo es informativo; si las salidas exceden entradas observadas se
omite ledger_balance, ya que el contrato no representa saldos negativos. Trust es
responsable de la contabilidad real por comercio.


## Despliegue y aceptación (fase 5)

Ver [la guía operativa](docs/operations.md) para configurar credenciales, levantar
Compose, autorizar el pool y ejecutar aceptación real con un archivo de intentos
estable. Docker usa un binario estático en distroless nonroot, filesystem de solo
lectura y Caddy como única entrada pública. El override local expone la API solo
en loopback. `scripts/live-smoke.py` utiliza únicamente la biblioteca estándar.

La suite incluye un receptor FastAPI real para verificar el contrato de webhook;
la aplicación de negocio Trust y su contabilidad siguen perteneciendo a su backend.
Las cinco fases tienen implementación y validación local. La aceptación contra
Test Wallet y el despliegue público siguen pendientes de credenciales y operación
real. La publicación del código no implica que esa aceptación haya sido aprobada.

## Demo sin dominio

Desde esta carpeta: `python3 scripts/demo.py`. El arranque obtiene una URL HTTPS
temporal, deja la API en localhost y configura el retorno sin frontend.
Requisitos y pasos: [guía de demo](docs/demo.md).
