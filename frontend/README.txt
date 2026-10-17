Trust frontend

INTEGRACIÓN LOCAL CON EL BACKEND

Desde backend/, preparar Postgres y las dependencias siguiendo su README:

    docker compose up -d db
    uv sync --frozen
    cp .env.example .env

Configurar JWT_SECRET (mínimo 32 caracteres) en backend/.env. Para la demo
local, configurar DEV_ENDPOINTS_ENABLED=true y OP_CLIENT=fake.
Desde frontend/, arrancar ambos servidores:

    python3 scripts/dev.py --seed

Abrir http://127.0.0.1:5173. El comando usa backend/.venv/bin/python,
conserva la configuración de base de datos y Open Payments del backend y
ajusta APP_WEB_URL y PUBLIC_API_URL para que la wallet vuelva al frontend
local con la misma sesión. Ctrl+C detiene ambos procesos. --seed es opcional;
crea los datos de demo mediante la API y requiere los endpoints dev activos.
Usuarios: marta@demo.trust, camilo@demo.trust y admin@demo.trust.
Contraseña de comercio y prestamista: DEMO_PASSWORD del backend (trust2026 por defecto).
El administrador requiere DEMO_ADMIN_PASSWORD independiente (mínimo 12 caracteres,
distinta de la contraseña de demo); si no está configurada, no puede iniciar sesión.
Opciones: --port 5173 --api-port 8000 --backend-python /ruta/a/python.

Si el backend ya está ejecutándose, usar solamente el proxy:

Ejecutar localmente (Python 3, sin instalar paquetes de producción):

    python3 scripts/serve.py --api http://localhost:8000

Abrir http://127.0.0.1:5173. El servidor proxy reenvía /api/v1 al backend
indicado, sin registrar credenciales ni cuerpos de peticiones.
En este caso, definir APP_WEB_URL=http://127.0.0.1:5173 en el backend antes
de arrancarlo. Usar siempre ese mismo origen para conservar sessionStorage
durante el retorno de la wallet (localhost y 127.0.0.1 son orígenes distintos).

Compilar:

    python3 scripts/build.py

Publicar dist/ en un servidor estático con /api/v1 como proxy al backend.
Configurar /op/return para servir /op/return.html conservando la query string.
Para una API en otro origen, establecer apiBaseUrl en src/config.js y permitir
el origen del frontend en CORS. No poner secretos en config.js.
testWalletUrl configura el enlace externo; por defecto abre https://wallet.interledger-test.dev/.

PRUEBAS CONTRA FASTAPI Y POSTGRES REALES

Con Postgres disponible en localhost:5433 y uv instalado, desde frontend/:

    uv run --project ../backend --with-requirements tests/requirements.txt playwright install chromium
    uv run --project ../backend --with-requirements tests/requirements.txt python -m unittest discover -s tests -p 'test_real_backend.py' -v

También disponible como npm run test:e2e. TRUST_E2E_PG_URL permite indicar
otra conexión de mantenimiento Postgres (por defecto
postgresql://trust:trust@127.0.0.1:5433/postgres). El usuario necesita CREATE
DATABASE. Cada ejecución crea una base única y la elimina al terminar;
no recrea ni trunca trust_dev o trust_test. Arranca API y frontend en puertos
libres, sin leer backend/.env y sin sustituir las respuestas del servidor.
Solo Open Payments/Puntored usan el simulador FakeOpClient del backend.

Comprueba registro, acceso y roles, publicación de solicitudes/ofertas,
aceptación, cancelación, rechazo/reintento de desembolso, retorno real de
wallet, QR, ventas en vivo, reparto y abonos, trazas, revisión de porcentaje,
administración, CSV, pago completo del crédito, ventas posteriores, errores
401/409, notificaciones y vistas móviles. Un observador de fetch valida las
peticiones/respuestas reales contra el YAML antes de la navegación, conservando
la respuesta original. Capturas, trazas Playwright y registros de las
operaciones verificadas: test-results/real-backend/ (ignorado por Git).
Las trazas incluyen sesiones de prueba; no publicarlas como datos de producción.

PRUEBAS AISLADAS DEL FRONTEND

    python3 -m venv .venv
    .venv/bin/pip install -r tests/requirements.txt
    .venv/bin/playwright install chromium
    .venv/bin/python -m unittest discover -s tests -p 'test_browser.py' -v
    node --test tests/format.test.js
    python3 -m unittest discover -s tests -p 'test_server.py' -v

El servidor local debe estar ejecutándose para las pruebas de navegador.
TRUST_TEST_URL cambia la URL base. Estas pruebas interceptan las llamadas
HTTP y validan sus cuerpos contra el OpenAPI; no mueven dinero real.
Capturas y trazas se escriben en test-results/ (ignorado por Git).

Regenerar contrato de pruebas y mapa de operaciones:

    .venv/bin/python scripts/contract.py

Código de aplicación: src/. Referencias originales: mockups/.
Análisis del contrato y límites de la comprobación: docs/API-CONTRACT.txt.
