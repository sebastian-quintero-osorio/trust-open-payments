Trust frontend

Ejecutar localmente (Python 3, sin instalar paquetes de producción):

    python3 scripts/serve.py --api http://localhost:8000

Abrir http://127.0.0.1:5173. El servidor proxy reenvía /api/v1 al backend
indicado, sin registrar credenciales ni cuerpos de peticiones.

Compilar:

    python3 scripts/build.py

Publicar dist/ en un servidor estático con /api/v1 como proxy al backend.
Configurar /op/return para servir /op/return.html conservando la query string.
Para una API en otro origen, establecer apiBaseUrl en src/config.js y permitir
el origen del frontend en CORS. No poner secretos en config.js.
testWalletUrl configura el enlace externo; por defecto no hay URL inventada.

Pruebas:

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
Análisis del contrato, diferencias y pendientes: docs/API-CONTRACT.txt.
El objetivo completo aún no se considera cumplido.
