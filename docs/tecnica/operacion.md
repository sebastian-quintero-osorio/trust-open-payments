# Desarrollo y operación de Trust

El entorno local más corto usa PostgreSQL, la API Python con `FakeOpClient` y el frontend servido con su proxy. La integración con Go requiere, además, una base de pagos, wallets y consentimiento. Los comandos de pruebas y simulación deben ejecutarse en entornos de desarrollo separados de los datos operativos.

## Requisitos declarados

| Componente | Requisitos del repositorio |
| --- | --- |
| Backend | Python `>=3.12,<3.13`, uv, PostgreSQL; Compose local con PostgreSQL 16 |
| Frontend | Python 3 para servir y compilar; Node para pruebas unitarias; Playwright para navegador |
| Landing | Node `>=22.22.0`, npm; `package.json` declara Astro `^7.4.0` y TypeScript `^6.0.3` |
| Go | `go.mod` declara Go `1.26.9`, SDK Open Payments `v0.5.0`; GCC para pruebas con detector de carreras |
| Despliegue integrado | Docker Compose y Traefik/Dokploy ya instalado con su red externa |

Las versiones anteriores proceden de los manifiestos, no de una política de actualización. Para instalaciones reproducibles se usan `backend/uv.lock`, `landing/package-lock.json` y `open-payments/go.sum`.

## Desarrollo local

Desde la raíz, preparar Python y PostgreSQL:

```bash
cd backend
docker compose up -d db
uv sync --frozen
test -e .env || cp .env.example .env
```

Editar `backend/.env` conservando cualquier configuración previa. Para la demo local:

```dotenv
DATABASE_URL=postgresql+psycopg://trust:trust@localhost:5433/trust_dev
JWT_SECRET=<secreto-aleatorio-de-al-menos-32-caracteres>
OP_CLIENT=fake
DEV_ENDPOINTS_ENABLED=true
WORKER_ENABLED=true
```

Generar un secreto, por ejemplo con `openssl rand -hex 32`, y sustituir el marcador; no usarlo literalmente. La contraseña de base `trust` corresponde únicamente al valor predeterminado de desarrollo del Compose local.

En otra terminal, desde la raíz:

```bash
cd frontend
python3 scripts/dev.py --seed
```

El lanzador usa `backend/.venv/bin/python`, inicia FastAPI en 8000 y el frontend en 5173, ajusta los retornos de wallet al origen local y detiene ambos al salir con Ctrl+C. Abrir `http://127.0.0.1:5173`; la documentación interactiva está en `http://127.0.0.1:8000/docs`. `--seed` es opcional y requiere endpoints de desarrollo habilitados.

El seed permite acceder como `marta@demo.trust` y `camilo@demo.trust` usando `DEMO_PASSWORD`, cuyo valor de demo predeterminado es `trust2026`. El administrador `admin@demo.trust` requiere **`DEMO_ADMIN_PASSWORD` independiente**, de al menos 12 caracteres y distinta de la contraseña pública. Si no se configura, la cuenta de administrador creada por el seed no permite acceder con la contraseña de demo.

Si se prefiere iniciar los procesos por separado, ejecutar desde `backend/`:

```bash
APP_WEB_URL=http://127.0.0.1:5173 PUBLIC_API_URL=http://127.0.0.1:8000 \
uv run uvicorn app.main:app --workers 1 --port 8000
```

Y desde `frontend/`:

```bash
python3 scripts/serve.py --api http://127.0.0.1:8000
```

Usar siempre el mismo origen del frontend al regresar de la wallet: `localhost` y `127.0.0.1` tienen almacenamientos de sesión distintos.

### Landing

Desde `landing/`:

```bash
npm ci
PUBLIC_SITE_URL=http://localhost:4321 \
PUBLIC_WAITLIST_ENDPOINT=http://127.0.0.1:8000/api/v1/waitlist npm run dev
```

Agregar `http://localhost:4321` a `CORS_ORIGINS` del backend y reiniciarlo. Ajustar el origen si Astro usa otro puerto. Para compilar y revisar el sitio estático:

```bash
PUBLIC_SITE_URL=https://trustservices.com.co \
PUBLIC_WAITLIST_ENDPOINT=/api/v1/waitlist npm run build
npm run preview
```

El endpoint relativo de ese build necesita un proxy al backend en el servidor que lo publique; `astro preview` por sí solo no configura ese proxy. Las variables públicas se fijan al compilar.

### Alternativa de mock HTTP

Para comprobar el límite HTTP sin Go, usar [tools/mock_op_service](../../backend/tools/mock_op_service/README.md). Se inicia en 8090 y se selecciona con `OP_CLIENT=http`, su URL, su API key y un secreto de webhook compartido. El mock conserva estados en memoria y no ofrece las garantías de persistencia de Go.

## Configuración de la API

La lista completa y los validadores están en [config.py](../../backend/app/core/config.py); [.env.example](../../backend/.env.example) sirve como plantilla. Las listas se separan por comas.

| Variables | Valor predeterminado o condición | Propósito |
| --- | --- | --- |
| `DATABASE_URL`, `JWT_SECRET` | Obligatorias; secreto mínimo de 32 caracteres | Persistencia y firma JWT |
| `JWT_EXPIRES_HOURS` | 168 | Duración de la sesión |
| `CORS_ORIGINS`, `TRUSTED_PROXIES` | Vacías | Orígenes permitidos y proxies autorizados a informar IP del cliente |
| `TIMEZONE` | `America/Bogota` | Días, ventanas de ventas y cierre diario |
| `MARKET_ASSET_CODE`, `MARKET_ASSET_SCALE` | `COP`, `2` | Asset de mercado |
| `OP_CLIENT` | `fake` | Selección fake o HTTP |
| `OP_SERVICE_BASE_URL`, `OP_SERVICE_API_KEY` | Vacías; necesarias para HTTP | Conexión interna con Go o mock |
| `OP_HTTP_TIMEOUT_SECONDS` | 10 | Timeout HTTP; health usa 2 segundos |
| `OP_FAKE_AUTO_COMPLETE` | `true` | Comportamiento de finalización del fake |
| `PUNTORED_WEBHOOK_SECRETS` | Vacía | Secretos HMAC aceptados; permite solapar claves durante rotación |
| `TRUST_REVENUE_WALLET_ADDRESS` | Vacía | Destino de ingresos Trust; su ausencia puede retener salidas |
| `TRUST_SHARE_BPS`, `TRUST_CUT_POLICY` | 2000, `prorated` | Reparto de la parte Trust |
| `TRUST_LEG_MIN` | 10000 unidades mínimas | Umbral de envío acumulado Trust: $100 en COP/2 |
| `MIN_LOAN_AMOUNT`, `AMOUNT_STEP`, `CUPO_CAP` | 50000000, 5000000, 1000000000 | $500.000 mínimo, pasos de $50.000 y cupo máximo $10.000.000 en COP/2 |
| `PCT_OPTIONS_BPS` | `1000,1500,2000` | Porcentajes publicados para solicitudes/ofertas |
| `DEFAULT_TERM_MONTHS` | 3 | Plazo predeterminado |
| `REPRICE_STEP_BPS`, `REPRICE_MAX_DELTA_BPS` | 250, 500 | Paso de revisión y margen superior cuando no hay solicitud |
| `MIN_MONTHS`, `MIN_SALES_PER_MONTH` | 3, 10 | Historial mínimo para elegibilidad |
| `SCORING_POLICY`, `SCORE_VOLUME_REF` | `v1`, 1000000000 | Política y referencia de volumen: $10.000.000 en COP/2 |
| `PENDING_LOAN_TTL_HOURS` | 48 | Vigencia de créditos pendientes |
| `WORKER_ENABLED`, `WORKER_INTERVAL_SECONDS` | `true`, 2 | Activación y espera entre ciclos |
| `DEV_ENDPOINTS_ENABLED` | `false` | Habilita rutas públicas de demo |
| `PUBLIC_API_URL`, `APP_WEB_URL`, `LANDING_URL` | Ajustar al entorno | Wallet falsa, retorno de consentimiento y redirección de formulario |
| `DEMO_PASSWORD`, `DEMO_ADMIN_PASSWORD` | Demo pública / administrador vacío | Credenciales de seed con tratamiento separado |
| `NOTIFY_SALE_SPLIT` | `true` | Avisos de reparto de venta |

No cambiar el asset sobre datos existentes sin una migración y revisión de unidades. La configuración monetaria almacena unidades mínimas, no pesos completos. Cambiar `TRUST_CUT_POLICY` afecta el procesamiento posterior de créditos existentes.

## Configuración de Go

[config.go](../../open-payments/internal/config/config.go) valida la configuración al arrancar. La [guía operativa Go](../../open-payments/docs/operations.md) describe el alta de wallets y la aceptación con proveedores externos.

| Variables | Condición |
| --- | --- |
| `DATABASE_URL` | URL PostgreSQL de la base propia del servicio |
| `INTERNAL_API_KEY` | Mismo valor que `OP_SERVICE_API_KEY`; mínimo 32 bytes |
| `PUBLIC_BASE_URL` | Origen HTTPS público del servicio y sus callbacks |
| `OP_CLIENT_WALLET_ADDRESS`, `OP_KEY_ID`, `OP_PRIVATE_KEY` | Wallet cliente, ID de clave registrada y PEM PKCS#8 Ed25519 |
| `ALLOWED_RETURN_URL_PREFIXES` | Lista HTTPS de retornos permitidos; excepción explícita para `DEMO_MODE` |
| `POOL_WALLET_ADDRESSES` | Wallets de recaudo; deben autorizarse antes de asignarse |
| `TRUST_WEBHOOK_URL`, `TRUST_WEBHOOK_SECRET` | Receptor HTTPS y secreto que Python acepta en `PUNTORED_WEBHOOK_SECRETS` |
| `SIM_ENABLED` | `false` por defecto |
| `SIM_PAYER_WALLET_ADDRESS`, `SIM_MAX_AMOUNT` | Requeridos al simular; wallet pagadora fuera del pool y monto máximo positivo |
| `INTERACTION_TIMEOUT_SECONDS`, `POLL_INTERVAL_SECONDS` | 600 y 2 por defecto |
| `TRUSTED_PROXY_CIDRS` | CIDR privados de proxies confiables |

Con las variables configuradas, `go run ./cmd/op-service` desde `open-payments/` escucha en 8080 y aplica migraciones. Para flujos de consentimiento se necesita acceso HTTPS público al callback. No es suficiente iniciar el proceso con una URL local HTTP.

## Trabajo en segundo plano

| Job Python | Frecuencia | Función |
| --- | --- | --- |
| J1 | Cada ciclo | Enviar payouts pendientes, lote de hasta 50 |
| J2 | Cada ciclo | Consultar payouts en proceso y contabilizar resultados |
| J3 | Cada ciclo | Consultar desembolsos y activar o devolver créditos a pendiente |
| J4 | Cada ciclo | Expirar créditos pendientes vencidos |
| J5 | Cada 30 segundos | Recuperar eventos desde el listado de pagos QR simulados |
| J6 | Una vez por día desde 00:05 local | Revisar créditos activos cuyo periodo venció |

Los fallos de un job se registran y no detienen los siguientes. Los errores transitorios al crear una salida conservan la clave y se aplazan diez segundos. Un resultado remoto fallido, reintentable y confirmado permite hasta cinco intentos automáticos, con esperas de 10 segundos, 1 minuto, 5 minutos y 5 minutos; cada nuevo intento envía el remanente.

Go ejecuta sus propios trabajos durables y reintenta webhooks mediante outbox. J5 solo concilia el listado del simulador: no consulta un extracto bancario Puntored real.

## Despliegue integrado

La [guía AWS existente](../../deploy/aws/README.txt) contiene la ubicación y los comandos del entorno preparado. [STATUS.txt](../../deploy/aws/STATUS.txt) conserva el resultado de su verificación del 17 de octubre de 2026, incluyendo uso de wallets de pruebas y consentimiento de recaudo pendiente. Ese registro no es una comprobación de disponibilidad actual.

Para preparar un entorno equivalente, revisar las plantillas [del stack](../../deploy/aws/.env.example) y [de Go](../../deploy/aws/op-service.env.example), completar secretos y dominios, y verificar que existen `dokploy-network`, los entrypoints `web`/`websecure` y el resolver `letsencrypt`. Ajustar los CIDR de proxies a las redes reales. Los archivos privados del servidor se conservan con permisos 600.

Desde `deploy/aws/`, con la configuración completada:

```bash
docker compose config --quiet
docker compose up -d --build --wait db backend frontend landing
```

Para incluir Go cuando sus credenciales estén configuradas:

```bash
docker compose --profile payments config --quiet
docker compose --profile payments up -d --build --wait
docker compose ps
docker compose logs --tail=100 backend op-service
```

`COMPOSE_PROFILES=payments` en el `.env` del stack conserva esa selección en ejecuciones posteriores. `config --quiet` valida sin imprimir secretos interpolados. PostgreSQL no publica un puerto del host en este despliegue.

El backend integrado fija `OP_CLIENT=http` y `DEV_ENDPOINTS_ENABLED=false`. Si Go no está disponible, el estado puede ser `degraded` mientras otras funciones, como lista de espera y autenticación, siguen disponibles. Un contenedor saludable no implica que los pagos estén listos.

Nginx sirve `/api/v1` mediante proxy y resuelve `/op/return` a la página HTML conservando los parámetros. Traefik termina HTTPS. Antes de utilizar recaudos, el operador consulta el pool, inicia `/v1/admin/pool-wallets/{id}/authorize` con idempotencia y aprueba en la wallet; después comprueba `authorized`.

### Respaldos y actualizaciones

`sh backup.sh`, desde `deploy/aws/`, genera respaldos privados de ambas bases. El script los deja en el mismo servidor; hace falta una copia externa para cubrir la pérdida del host. Conservar de forma segura la clave de cifrado derivada de `OP_PRIVATE_KEY` mediante la custodia de la clave privada correspondiente.

Antes de actualizar, conservar respaldos y las imágenes anteriores, revisar cambios de esquema y asignar un `RELEASE_TAG` nuevo. Copiar fuentes sin sobrescribir configuraciones privadas y reconstruir. No usar `docker compose down -v` para actualizar: elimina el volumen de datos. Una vuelta a la imagen anterior no deshace migraciones.

El repositorio no incluye un procedimiento integral automatizado de restauración de ambas bases y reconciliación con wallets. Una restauración debe probarse en un entorno aislado antes de reactivar workers financieros sobre ella.

## Diagnóstico

| Síntoma | Comprobación y acción |
| --- | --- |
| Salud `down` | Verificar `db`, conectividad y logs PostgreSQL; la API responde 503 cuando su base falla |
| Salud `degraded` | Inspeccionar `op`, `op_client` y `warnings`; comprobar URL, API key y coincidencia de asset |
| `pool_exhausted` | Revisar wallets disponibles y consentimientos en el pool, no solo `/health` |
| Regreso de wallet sin sesión | Comparar origen usado al entrar con `APP_WEB_URL`; conservar `/op/return` y query string |
| Crédito sigue `disbursing` | Consultar pago Go y worker; el retorno del navegador no confirma el movimiento |
| Payout `processing` prolongado | Revisar recurso Go y conciliación de outgoing incierto antes de intentar otra operación |
| Payout `held` | Revisar cuenta activa, revocación del grant y wallet receptora; la API admin exige condiciones válidas para reintentar |
| Payout `failed_final` | Revisar `failure_code` y `sent_total`; el reintento administrativo conserva el monto ya enviado |
| Webhook 401 | Comprobar secreto compartido, cuerpo sin modificar y reloj dentro de la tolerancia de cinco minutos |
| Evento `unmatched` | Revisar cuenta remota, wallet y asset conservados en `webhook_events` |
| Lista de espera 429 | Revisar límite de 10 peticiones por IP/minuto y configuración de proxies confiables |
| Ruta `/dev` responde 404 | Comportamiento esperado con demo deshabilitada; en Go la simulación tiene su bandera independiente |

La revocación administrativa se guarda primero en Python y luego se comunica a Go. Si falla esa segunda llamada, la cuenta permanece revocada localmente; repetir la revocación permite completar la operación remota. No corregir estados financieros con cambios manuales de base sin reconciliar el resultado externo.
