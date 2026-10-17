# Verificación y pendientes técnicos

La documentación describe el árbol local revisado el 17 de octubre de 2026. Las comprobaciones de código y contratos no certifican la ejecución de transferencias externas ni la disponibilidad de un despliegue.

## Comprobaciones de esta revisión

| Comprobación ejecutada | Resultado |
| --- | --- |
| Importación de FastAPI y generación de OpenAPI sin iniciar el ciclo de vida | Las 48 operaciones coinciden en método y ruta con el contrato compartido |
| Catálogos de esta documentación contra los YAML | Incluyen las 48 operaciones Trust y las 17 operaciones Go |
| Registro de modelos SQLAlchemy sin conectar a PostgreSQL | 15 tablas de negocio |
| Copias de contratos | `paths` y `components` coinciden; Trust también coincide byte a byte; Go difiere en una línea descriptiva |
| Mapa de operaciones frontend | Método, ruta y condición pública coinciden con el contrato Trust |
| Ejecución de `build_terms` y `split_sale` | Ejemplo de $48.000: abono $7.200, comercio $40.800, Trust $81,50 y prestamista $7.118,50 |
| Ejecución de `cupo_for` | Promedio de $10.000.000, grado A y elegibilidad producen cupo $3.750.000 con valores predeterminados |
| Navegación de la documentación nueva | 113 enlaces locales y sus anclas comprobados; bloques de código cerrados |

Las importaciones utilizaron configuración aislada, sin leer `.env`, abrir conexiones PostgreSQL ni arrancar workers. La comprobación de rutas no valida por sí sola todas las respuestas ni sustituye la suite de contrato con base de datos. Los diagramas se entregan como código Mermaid; no se verificó su renderizado en un visor gráfico en esta revisión.

La revisión no leyó los archivos privados `.env`, no ejecutó transferencias y no cambió infraestructura. Docker devolvió denegación de acceso a su socket. No se ejecutaron en esta tarea las suites dependientes de PostgreSQL, navegador o wallets externas; los resultados históricos de otros archivos no se presentan como pruebas nuevas.

## Pruebas disponibles

| Área | Comando y ubicación | Alcance y condiciones |
| --- | --- | --- |
| Backend | `uv run pytest -q`, desde `backend/` | Reglas, persistencia PostgreSQL, autorización, concurrencia, worker y contratos; base exclusiva terminada en `_test` |
| Estilo Python | `uv run ruff check .`, desde `backend/` | Comprobación estática configurada en `pyproject.toml` |
| Demo de API | `uv run python scripts/smoke_e2e.py`, desde `backend/` | Flujo HTTP con fake y base desechable |
| Límite HTTP de pagos | `./scripts/smoke_http.sh`, desde `backend/` | Flujo del cliente HTTP contra mock de Go |
| Frontend unitario | `npm run test:unit`, desde `frontend/` | Pruebas Node de funciones del cliente |
| Servidor frontend | `python3 -m unittest discover -s tests -p 'test_server.py' -v`, desde `frontend/` | Servidor estático, proxy y rutas locales |
| Navegador aislado | `python -m unittest discover -s tests -p 'test_browser.py' -v`, desde `frontend/` | Playwright con respuestas HTTP interceptadas; requiere dependencias y servidor local |
| Navegador con backend | `npm run test:e2e`, desde `frontend/` | FastAPI y PostgreSQL reales; crea y elimina una base única; pagos externos reemplazados por fake |
| Landing | `npm run check`, `npm run contrast`, `npm run build`, desde `landing/` | Tipos/plantillas, contraste y generación estática |
| Go completo | `./scripts/check.sh`, desde `open-payments/` | Formato, generación, análisis, race tests, seguridad y OpenAPI; exige herramientas y base de pruebas |
| Aceptación de wallets | `scripts/live-smoke.py`, según [guía Go](../../open-payments/docs/operations.md) | Requiere credenciales, consentimientos y planificación de operaciones externas |

Las fixtures Python recrean el esquema y truncan tablas. `backend/docker-compose.yml` crea `trust_dev`, no `trust_test`: crear la base de pruebas una vez antes de la suite, por ejemplo desde `backend/`:

```bash
docker compose exec db psql -U trust -d trust_dev -c 'CREATE DATABASE trust_test'
```

Usar una base exclusiva. No configurar `TEST_DATABASE_URL` con datos que se deban conservar. La suite del frontend con backend utiliza `TRUST_E2E_PG_URL` como conexión de mantenimiento y necesita permiso para crear bases. Las instrucciones de instalación de Playwright están en [frontend/README.txt](../../frontend/README.txt).

## Límites y trabajo pendiente

| Área | Estado implementado | Consecuencia y siguiente trabajo |
| --- | --- | --- |
| Puntored/Bre-B | Webhook y simulador QR en Go; sin adaptador bancario productivo en el recorrido revisado | Implementar y validar la entrada real antes de presentar el flujo como cobro bancario integrado |
| Consentimiento de recaudo | Pool y autorización interactiva implementados; el registro de despliegue conserva una autorización pendiente | Verificar consentimiento en el entorno que se vaya a utilizar; `/health` no lo acredita |
| Pagos externos | SDK, firmas, grants, quotes, transferencias y recuperación implementados | La aceptación con wallets reales es una comprobación separada de las pruebas locales y del uso de `OP_CLIENT=http` |
| Migraciones Python | `create_all` al arrancar | Incorporar migraciones versionadas antes de evolucionar esquemas con datos persistentes |
| Escalado | Worker Python y limitador de waitlist dentro del proceso | Mantener un proceso/instancia hasta introducir coordinación y límites compartidos |
| Reversos | Tipos distintos de `qr_payment.received` se guardan e ignoran | Definir flujo contable y financiero de devolución/reversión antes de incorporarlo |
| Conciliación | J5 consume el listado del simulador | Añadir fuente y procedimiento de conciliación para la integración bancaria que se adopte |
| Resultado incierto | Go conserva checkpoints y busca outgoing por metadata | Requiere intervención si la operación no puede determinarse; no automatizar reenvíos ciegos |
| Copias de contratos | Dos copias por API; diferencia textual en el contrato Go | Automatizar o comprobar sincronización para evitar divergencias futuras |
| Políticas de crédito | Score determinístico `v1`, cargo fijo y términos en snapshots | No hay modelo de riesgo entrenado ni cálculo de tasa efectiva anual; cualquier cambio debe definir versión y efecto sobre créditos vigentes |
| Política de comisión | `trust_share_bps` se congela; `TRUST_CUT_POLICY` se lee globalmente por venta | Versionar o congelar también la política antes de cambiarla con créditos activos |
| Historial de scoring | Incluye ventas seed y retenidas; elegibilidad basada en meses activos históricos | Mantener seeds en entornos de demo y revisar explícitamente las fuentes admitidas al cambiar la política |
| Sesiones | JWT con vencimiento, sin refresh/MFA/recuperación ni revocación individual en contrato | Diseñar el ciclo de recuperación y control de sesiones antes de exigir esas capacidades al producto |
| Respaldos | Script de ambas bases en disco local del servidor | Añadir copia externa y probar restauración coordinada con las claves y estados financieros |
| Operación observable | Health, logs, eventos y dashboard admin | Definir alertas para fondos retenidos, colas estancadas, grants revocados y errores recurrentes; health no demuestra conciliación completa |
| Cierre diario | Cursor diario; un periodo por crédito en cada ejecución | Tras interrupciones largas, revisar periodos atrasados; no asumir recuperación de todos los periodos en un solo ciclo |
| Móvil | Contrato y enlace de retorno disponibles | El cliente nativo no está implementado en este árbol |

La regla `is_late` actual acumula periodos incumplidos y no los reinicia al cumplir después. Esto debe conservarse como comportamiento explícito o cambiarse junto con sus pruebas; no describirla como “dos meses consecutivos”.

## Evidencia principal

Los inventarios de persistencia se contrastan con [app/models.py](../../backend/app/models.py) y las [migraciones Go](../../open-payments/migrations/). Las reglas monetarias proceden de [policies](../../backend/app/policies/), los trabajos de [worker.py](../../backend/app/worker.py) y [worker.go](../../open-payments/internal/application/worker.go), y la configuración de despliegue de [compose.yaml](../../deploy/aws/compose.yaml).

El [estado de despliegue registrado](../../deploy/aws/STATUS.txt) y la [verificación del diagrama existente](../arquitectura/verificacion.txt) tienen sus propios alcances y fechas. La documentación general no convierte esas evidencias históricas en una nueva validación operativa.
