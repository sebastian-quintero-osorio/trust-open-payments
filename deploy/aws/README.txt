Trust en la instancia AWS 54.210.198.244

Este despliegue utiliza Docker Compose y el Traefik existente de Dokploy.
No instala otro proxy ni modifica las aplicaciones Trace. Necesita la red
externa dokploy-network, los entrypoints web/websecure y el resolver letsencrypt.
TRUSTED_PROXIES debe ajustarse si cambia la subred de dokploy-network.

Acceso:
    ssh -i ~/.ssh/trust-aws-codex.pem root@54.210.198.244
    cd /opt/trust/deploy/aws

Configuración privada: /opt/trust/deploy/aws/.env (permisos 600).
No ejecutar docker compose config sin --quiet en registros compartidos:
la salida completa contiene secretos interpolados.

Servicios:
    docker compose ps
    docker compose logs --tail=100 backend
    docker compose up -d --build --wait db backend frontend landing

Postgres 16 usa el volumen trust_postgres_data y una red privada, sin puerto
publicado. Hay dos bases: trust (FastAPI) y op_service (Open Payments).
El backend accede a db:5432. Las tablas de Trust se crean al arrancar;
create_all no migra columnas existentes. No ejecutar down -v ni borrar el
volumen al actualizar. Los servicios reinician automáticamente con Docker.

Open Payments real:
1. Crear op-service.env a partir de op-service.env.example, con permisos 600.
   Configurar wallet, clave Ed25519 PKCS#8 registrada, key ID y pool de wallets.
   El PEM admite saltos reales entre comillas simples en el archivo env.
2. Configurar TRUST_REVENUE_WALLET_ADDRESS en .env y verificar que las wallets
   usen el asset COP/2 esperado por Trust.
3. Ejecutar:
       docker compose --profile payments up -d --build --wait
4. Comprobar /api/v1/health: db=ok, op=ok, op_client=http, warnings=[].
5. Consultar /v1/admin/pool-wallets por la red interna usando X-Internal-Api-Key.
   Para cada wallet de recaudo no autorizada, iniciar el consentimiento con
   POST /v1/admin/pool-wallets/{id}/authorize y un Idempotency-Key único.
   El propietario debe abrir authorization_url y aprobar en su wallet de
   Interledger antes de authorization_expires_at. Confirmar después que el
   estado cambió a authorized. Health=ok no implica consentimiento concedido.

Si Interledger muestra que el grant ya fue aceptado, pero Trust sigue en
awaiting_authorization, completar el retorno antes de que venza el intento:
abrir https://<auth-host>/interact/<interaction-id>/<nonce>/finish en la misma
sesión del navegador que aprobó. Es la ruta de finalización del proveedor;
no añadir /finish al callback de Trust ni fabricar hash/interact_ref. El
proveedor exige su cookie de sesión, por lo que curl desde el servidor no
sustituye este paso. No regenerar un consentimiento aceptado mientras se
pueda recuperar su retorno. Confirmar authorized consultando el servicio.

Estado instalado: COMPOSE_PROFILES=payments en .env; Open Payments activo.
PAY_DOMAIN=pay.trustservices.com.co, con DNS y HTTPS configurados.
OP_ALLOWED_RETURN_URL_PREFIXES conserva el retorno del frontend y los
retornos adicionales del .env de Open Payments. Al actualizar las claves
INTERNAL_API_KEY o TRUST_WEBHOOK_SECRET, sincronizar OP_SERVICE_API_KEY y
PUNTORED_WEBHOOK_SECRET en el .env del despliegue para que ambos servicios
usen los mismos valores. Conservar las claves de las bases existentes.
Las wallets proporcionadas usan el entorno de pruebas interledger-test.dev,
COP/2 y SIM_ENABLED=true, con SIM_MAX_AMOUNT=10000000 ($100.000 COP).
El QR abre el formulario del simulador; para pagar, comprador_trust necesita
saldo de pruebas y autorización vigente como sim_payer. El reparto al prestamista
requiere un crédito active. No equivalen a una integración de dinero productivo.

El backend siempre usa OP_CLIENT=http; DEV_ENDPOINTS_ENABLED=false.
Sin credenciales de Open Payments, el perfil payments queda pendiente y
/api/v1/health informa degraded/op=down. Esto permite publicar la landing,
la lista de espera y la aplicación sin simular pagos ni inventar wallets.
Los préstamos/recaudos que requieren wallets no están operativos hasta
activar y verificar Open Payments. No se ejecutan transferencias de dinero
como parte del despliegue.

DNS y HTTPS:
LANDING_DOMAIN, APP_DOMAIN, API_DOMAIN y PAY_DOMAIN se leen de .env.
Cada dominio necesita un registro A hacia 54.210.198.244. Si app/api/pay
todavía no resuelven, se pueden usar los hosts temporales sslip.io escritos
en .env. Tras crear DNS, actualizar .env y ejecutar:
    docker compose --profile payments up -d --build --wait
Omitir --profile payments mientras falten sus credenciales.
Traefik emite certificados y redirige HTTP a HTTPS automáticamente.
La landing envía /api/v1/waitlist al backend. El frontend usa el proxy
/api/v1 en su mismo origen y sirve /op/return conservando los parámetros.

Backup manual de ambas bases (archivos privados):
    sh backup.sh
Los backups quedan en backups/ en la misma instancia; copiar a almacenamiento
externo para protegerse ante pérdida de la instancia. No hay backup remoto
ni recuperación automática configurados por este despliegue.

Actualizar código:
Copiar fuentes a /opt/trust sin sobrescribir .env ni op-service.env, asignar
un RELEASE_TAG nuevo en .env y ejecutar docker compose up -d --build --wait.
Conservar las imágenes anteriores para poder volver a la versión previa.
Un rollback de código no revierte cambios del esquema de la base de datos.
