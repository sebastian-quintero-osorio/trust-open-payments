# Demo local sin dominio

Requisitos: Docker con Compose, Python 3 y `cloudflared` en PATH.
Instalación oficial: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/.

En `open-payments/.env` configurar solamente las credenciales reales:

```dotenv
POSTGRES_PASSWORD=REEMPLAZAR_CON_PASSWORD_ALEATORIO
INTERNAL_API_KEY=REEMPLAZAR_CON_OPENSSL_RAND_HEX_32
OP_CLIENT_WALLET_ADDRESS=https://ilp.interledger-test.dev/tu-wallet-trust
OP_KEY_ID=identificador-de-la-clave-publica-registrada
OP_PRIVATE_KEY='-----BEGIN PRIVATE KEY-----
CONTENIDO_REAL_DE_LA_CLAVE_ED25519_PKCS8
-----END PRIVATE KEY-----'
POOL_WALLET_ADDRESSES=https://ilp.interledger-test.dev/tu-wallet-trust
```

No confundir el identificador o la clave pública con la clave privada. Esta última
se obtiene al generar el par registrado en Developer Keys y nunca se comparte por
chat ni se agrega a Git. Si se perdió, generar otro par y registrar su pública;
no cambiar la clave de una base de datos con grants sin plan de migración, porque
también se usa para proteger sus tokens almacenados.

Desde `open-payments` ejecutar:

```bash
python3 scripts/demo.py
```

Para validar primero las credenciales, sin abrir el túnel ni ejecutar pagos:

```bash
python3 scripts/demo.py --check
```

Docker debe estar iniciado. El script comprueba el formato de la configuración;
la correspondencia entre la clave privada y la pública registrada se verifica al
hacer solicitudes firmadas a Open Payments.

El script obtiene una URL HTTPS temporal y la inyecta como PUBLIC_BASE_URL sin
editar `.env`. No necesita dominio, PAY_DOMAIN, certificado ni configurar Caddy
manualmente. Un gateway HTTP local conserva la restricción de rutas públicas:
solo `/v1/health`, `/v1/interact/finish/*` y `/qr/*` llegan al túnel.

La API interna queda en `http://127.0.0.1:8080`; el túnel apunta exclusivamente al
gateway `127.0.0.1:8081`. No apuntarlo directamente al puerto privado 8080.
TRUSTED_PROXY_CIDRS queda vacío y no se confía en cabeceras de IP. Por ello el límite
de 5 pagos QR por minuto se comparte entre visitantes del gateway durante la demo.
Firmas, consentimiento, API key, límites de monto e idempotencia siguen activos.

Sin frontend, ALLOWED_RETURN_URL_PREFIXES se configura automáticamente con
`PUBLIC_BASE_URL/v1/health`. Es un retorno de navegador, no una confirmación del
resultado financiero: consultar el recurso hasta su estado terminal. Si ya hay un
frontend HTTPS, iniciar con `DEMO_RETURN_URL=https://tu-frontend/retorno`.
El script ignora el valor antiguo de ALLOWED_RETURN_URL_PREFIXES en `.env` para
evitar depender de un dominio que todavía no existe.

La URL cambia al reiniciar el túnel. Finalizar las autorizaciones de la sesión
antes de detenerla; los QR creados conservan la URL de esa sesión y conviene crear
una nueva cuenta de demo si se cambia de túnel. Ctrl+C detiene los procesos; los
datos PostgreSQL se conservan. No borrar volúmenes para resolver pagos inciertos.

## QR, ventas y distribución

Todas las funcionalidades siguen disponibles. Para activar las ventas por QR:

```dotenv
SIM_ENABLED=true
SIM_PAYER_WALLET_ADDRESS=https://ilp.interledger-test.dev/cliente-demo
SIM_MAX_AMOUNT=50000
TRUST_WEBHOOK_URL=https://url-publica-del-backend/api/v1/webhooks/puntored/payments
TRUST_WEBHOOK_SECRET=REEMPLAZAR_CON_SECRETO_COMPARTIDO_DE_32_BYTES_O_MAS
```

El webhook tiene que apuntar al backend Trust que recibe la venta y ordena su
distribución. El túnel de op-service no publica ese backend: si también es local,
necesita su propia URL HTTPS. No sustituirlo por health ni desactivar su firma.
Las wallets de recaudo, cliente y destinatarios deben usar el mismo asset. En la
Test Wallet, usar usuarios distintos para mantener saldos independientes.

Para `live-smoke.py` usar las credenciales en el entorno y PUBLIC_BASE_URL con la
URL impresa por la sesión. El script es una prueba aislada: consultar
[operaciones](operations.md) antes de usar `--manual-distribution`, para evitar
duplicar repartos del backend real. Dejar ALLOWED_RETURN_URL_PREFIXES vacío en
ese script si se usa el retorno automático.

## Validaciones

`./op-service check-config` valida las variables cargadas en el entorno sin
conectar a PostgreSQL ni Open Payments, y sin imprimir secretos. No carga `.env`
por sí mismo; Compose sí lo hace.

Las pruebas locales con PostgreSQL y el receptor FastAPI no requieren credenciales
de Test Wallet. La aceptación real requiere la clave privada correcta, conexión a
Internet, consentimiento en navegador y fondos de prueba. No se considera aprobada
hasta verificar los pagos y los webhooks reales.
