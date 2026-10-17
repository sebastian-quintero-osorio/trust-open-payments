# Documentación técnica de Trust

Trust implementa un mercado de crédito para comercios con pago mediante retenciones sobre ventas. Esta documentación permite desarrollar, integrar y operar los componentes actuales, e identifica dónde termina la implementación y dónde empiezan las dependencias externas o el trabajo pendiente.

Revisión: 17 de octubre de 2026. Alcance: código y configuración del repositorio, incluido el árbol de trabajo local sobre `e067c64`. El estado de un servidor publicado debe comprobarse por separado; los registros de despliegue son evidencia fechada.

| Documento | Contenido |
| --- | --- |
| [Arquitectura](tecnica/arquitectura.md) | Componentes, responsabilidades, comunicaciones, frontend y modos de integración |
| [Dominio y flujos](tecnica/dominio-y-flujos.md) | Cupo, matching, estados del crédito, desembolso, reparto y revisión del porcentaje |
| [Modelo de datos](tecnica/modelo-de-datos.md) | Tablas, relaciones, invariantes, contabilidad e idempotencia |
| [APIs e integración](tecnica/apis.md) | Convenciones, autenticación, webhook y catálogo completo de operaciones |
| [Desarrollo y operación](tecnica/operacion.md) | Arranque local, variables, despliegue, respaldos y diagnóstico |
| [Verificación y pendientes](tecnica/verificacion-y-pendientes.md) | Comprobaciones de esta revisión, suites disponibles y límites que afectan la operación |

Para incorporarse al desarrollo, leer arquitectura y dominio antes del modelo de datos. Para integrar un cliente, comenzar por APIs. Para preparar un entorno, seguir operación y revisar los pendientes.

## Fuentes y mantenimiento

Las descripciones del comportamiento se apoyan en los módulos enlazados en cada documento. [Trust API](../contracts/trust-api.openapi.yaml) y [op-service API](../contracts/op-service.openapi.yaml) definen las formas de las peticiones y respuestas compartidas. Las [specs](../backend/specs/README.md) desarrollan los criterios de aceptación; el [diseño del backend](../backend/docs/diseno-backend.md) conserva decisiones de diseño más extensas.

Existen copias de contratos consumidas por herramientas: el frontend genera su mapa desde [trust-api.openapi.yaml](../trust-api.openapi.yaml), y Go incorpora [open-payments/op-service.openapi.yaml](../open-payments/op-service.openapi.yaml). Su relación y la diferencia textual encontrada se detallan en [APIs](tecnica/apis.md#contratos-y-copias).

Cuando cambie una regla, actualizar su explicación y los ejemplos; cuando cambie una operación, sincronizar los contratos y regenerar los artefactos consumidores. Mantener junto al cambio las pruebas de comportamiento correspondientes. No registrar secretos de `.env`, tokens de usuario ni claves de wallets en documentación o capturas.

## Arquitectura visual existente

El repositorio incluye una [imagen PNG](arquitectura/arquitectura-trust.png), un [SVG editable](arquitectura/arquitectura-trust.svg), su [generador](arquitectura/generar.py) y una [verificación de correspondencias](arquitectura/verificacion.txt). Esa vista simplificada representa el recorrido frontend–backend–pagos; el documento de arquitectura añade la landing y los modos de ejecución.
