# op-service

Servicio Go interno de Trust. El contrato original es `op-service.openapi.yaml`.
La fase 1 incluye servidor, configuración, modelos, validación, persistencia,
idempotencia y CI. Los flujos financieros aún no están implementados: sus rutas
responden `503 upstream_error`, sin crear recursos ni fingir pagos exitosos.

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
| `ALLOWED_RETURN_URL_PREFIXES` | Prefijos HTTPS separados por comas |

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
govulncheck, OpenAPI y Gitleaks sobre archivos e historial. El workflow usa los
mismos comandos, permisos mínimos y acciones fijadas por SHA; cualquier fallo
hace fallar el check `op-service / quality-security`. Al publicar, el administrador
debe exigir ese check en la protección de la rama para impedir merges ante fallos.
No se ha configurado protección remota ni ejecutado GitHub Actions en esta entrega
local, conforme a la instrucción de realizar commits sin push.

## Integración pendiente

Las pruebas reales de Open Payments requieren una wallet Trust, clave Ed25519
registrada y wallets de usuarios separados para recaudo, ingresos y pagador demo.
El backend FastAPI aún no existe en este repositorio; la integración final requiere
su API y receptor de webhooks. No se sustituyen estas validaciones con mocks.

Referencias: [Open Payments](https://openpayments.dev/),
[pgx](https://github.com/jackc/pgx),
[JSON Schema validator](https://github.com/santhosh-tekuri/jsonschema).
