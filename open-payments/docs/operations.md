# Configuración y aceptación final

La implementación y las pruebas locales de las cinco fases están disponibles.
Las credenciales reales, la aprobación de consentimientos y las pruebas contra Test
Wallet quedan pendientes por decisión del usuario. No se han publicado commits.

## 1. Preparar identidades

En Interledger Test Wallet crear usuarios separados para Trust/recaudo, Trust
Ingresos y Cliente demo: las wallets de un mismo usuario y asset comparten saldo.
Preparar también la wallet del prestamista y la cuenta propia del comercio.

Registrar en Developer Keys de la wallet cliente Trust la clave pública Ed25519.
Guardar su identificador en OP_KEY_ID y la privada correspondiente en
OP_PRIVATE_KEY, PEM PKCS#8. No se necesitan contraseñas de los usuarios dentro del
servicio: el consentimiento se aprueba en su navegador. La wallet del prestamista
y el pagador demo necesitan saldo de prueba suficiente, incluidas comisiones.

Para fase 2 son indispensables OP_CLIENT_WALLET_ADDRESS, OP_KEY_ID, OP_PRIVATE_KEY,
una wallet emisora con saldo y una receptora. Para la demo completa, añadir el pool,
el pagador demo, los receptores de cada distribución y el webhook de Trust.

Referencias oficiales: [claves](https://openpayments.dev/identity/client-keys/),
[grants](https://openpayments.dev/identity/grants/),
[SDK Go](https://github.com/interledger/open-payments-go).

## 2. Configurar el despliegue

Copiar `.env.example` a un archivo protegido fuera del repositorio, completar sus
valores y limitar permisos a 0600. DATABASE_URL se construye en Compose usando
PostgreSQL privado; elegir una contraseña hexadecimal aleatoria para evitar caracteres
reservados en la URL. Se pueden generar secretos independientes con
`openssl rand -hex 32`. OP_PRIVATE_KEY puede ser un valor multilínea entre comillas
simples en el archivo de entorno de Compose.

PUBLIC_BASE_URL y PAY_DOMAIN deben apuntar al host público de Caddy; los callbacks
requieren HTTPS y acceso desde el navegador. Caddy publica solo health, callbacks y
QR. PostgreSQL y los endpoints internos no tienen puertos públicos. La Trust API debe
unirse a la red Docker `trust-payments` y llamar a `http://op-service:8080` con la API
key interna. TRUST_WEBHOOK_URL debe ser HTTPS público y TRUST_WEBHOOK_SECRET debe
coincidir con el del receptor Trust. Solo activar SIM_ENABLED en la demo prevista.

La configuración opcional TRUSTED_PROXY_CIDRS reconoce únicamente el Caddy de este
Compose (172.30.0.2/32). Las direcciones X-Forwarded-For de otros clientes se ignoran.
Si se cambia la subred Docker, actualizar las IPs fijas y ese CIDR conjuntamente.

Desde `open-payments/`:

```bash
docker compose --env-file /ruta/protegida/op-service.env config --quiet
docker compose --env-file /ruta/protegida/op-service.env up --build -d
docker compose --env-file /ruta/protegida/op-service.env ps
```

Para ejecutar pruebas desde el host, añadir `-f compose.yaml -f compose.local.yaml`;
el override publica 8080 solamente en 127.0.0.1. El contenedor financiero corre como
65532, sin capacidades, con filesystem de solo lectura y no-new-privileges. El probe
`/op-service healthcheck` no necesita secretos. Las migraciones se aplican al arrancar;
hacer backup antes de actualizar y conservar la clave privada que cifra los grants.

## 3. Autorizar el pool una vez

Con las variables del entorno configuradas y la API interna accesible:

```bash
python3 scripts/live-smoke.py check
python3 scripts/live-smoke.py prepare --state /ruta/protegida/preparacion.json
```

El script muestra URLs de consentimiento, nunca tokens ni claves. Aprobar las wallets
de recaudo con la sesión Trust y el pagador con la sesión Cliente demo; luego repetir
el comando con el mismo archivo. Código 2 significa que falta consentimiento manual.
La API de administración devuelve estados authorized/assigned cuando está listo.
No reenviar el grant por polling: el redirect público es obligatorio.

También se puede invocar POST /v1/admin/pool-wallets/{id}/authorize con API key e
Idempotency-Key, abrir authorization_url y consultar GET /v1/admin/pool-wallets.
Si se rechazó o expiró un consentimiento, usar un nuevo intento de autorización;
no reutilizar una respuesta idempotente del intento anterior.

## 4. Aceptación financiera real

El script de demo usa una cuenta de humo nueva, sin asociarla a un comercio real de
Trust, y distribuye 20%/75%/5% únicamente para probar op-service. No usar distribución
manual junto con un backend que ya distribuye automáticamente ese mismo recaudo.
Para comprobar la lógica real de créditos y reparto de Trust, ejecutar además las
pruebas del backend propietario; este repositorio no contiene su implementación.

Configurar DEMO_LENDER_WALLET_ADDRESS, DEMO_MERCHANT_WALLET_ADDRESS y
DEMO_TRUST_INCOME_WALLET_ADDRESS. DEMO_CREDIT_VALUE (10000 por defecto) y
DEMO_SALE_VALUE (4800) son enteros en unidades mínimas del asset receptor; escoger
valores que permitan pagar las comisiones. OP_SMOKE_BASE_URL apunta a la API interna,
por defecto http://127.0.0.1:8080.

```bash
python3 scripts/live-smoke.py demo --manual-distribution \
  --state /ruta/protegida/demo-financiera.json
```

Aprobar el consentimiento del prestamista cuando lo solicite y repetir el comando
con el mismo archivo. El script espera crédito completado, venta confirmada, webhook
aceptado y tres transferencias completadas. El estado conserva la misma identidad de
cada POST ante timeout. Un HTTP 200 del webhook prueba recepción, no la contabilidad
interna de Trust: su contrato también acepta eventos de cuentas desconocidas.

Si queda processing o hay un fallo, inspeccionar el recurso. No cambiar el archivo
ni la clave de pago para resolver un resultado incierto. El listado de conciliación
se consume con after/next_after y conserva el mismo webhook_event en cada consulta.
Verificar además en las wallets los montos debitados y recibidos: los pagos de otras
monedas o con comisiones no tienen necesariamente el mismo debit y receive.

## 5. Pruebas locales reproducibles

```bash
./scripts/install-tools.sh
python3 -m venv /tmp/op-service-validation
source /tmp/op-service-validation/bin/activate
pip install -r scripts/requirements-tools.txt
export PATH="$(go env GOPATH)/bin:$PATH"
export TEST_DATABASE_URL='postgres://usuario@localhost/op_service_test'
./scripts/check.sh
```

La suite crea esquemas aislados para los flujos y un receptor FastAPI real en
loopback con SQLite para probar firma y deduplicación; ese receptor es una fixture,
no un backend de producción. Se comprueban pérdida de respuesta, reinicio, saldo
insuficiente, expiración, límites de consentimiento, revocación, reutilización segura
del pool, concurrencia, cuerpos estables del webhook y orden de commit del cursor.
Las claves locales se generan para cada prueba y nunca se publican en Test Wallet.

El workflow añade construcción de imagen, validación de Compose y Caddy. Para exigir
checks en un PR se debe configurar protección de rama al publicar en GitHub; la
petición actual autoriza únicamente commits locales.

## Recuperación operativa

Un outgoing con resultado incierto se busca por metadata.op_operation_id. Mientras
no pueda confirmarse su resultado, se mantiene processing y no se crea otra salida.
Si un listado no lo muestra, eso no demuestra que no se haya ejecutado: conciliar
con el emisor antes de permitir un nuevo intento de negocio.

Una rotación interrumpida bloquea el grant. Reautorizar la wallet con un intento
nuevo y volver a asignar una cuenta si la anterior está revoked; el historial de
cuentas conserva los enlaces anteriores y nunca reactiva la cuenta revocada.
Un fallo grant_revoked no se reintenta como pago automáticamente.

Los webhooks fallidos se reintentan 1 s, 5 s, 30 s y 2 min. Tras agotar intentos,
Trust debe reconciliar por el listado; mantener event_id y los bytes del evento.
El outbox guarda el body crudo y la firma usa un timestamp fresco en cada envío.
Cambiar OP_PRIVATE_KEY requiere reencryptar los registros con la clave anterior;
no descartar esa clave mientras existan datos o backups cifrados con ella.
