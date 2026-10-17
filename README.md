# Trust

Trust conecta comercios que necesitan crédito con prestamistas y aplica una parte de las ventas al pago del préstamo. La API calcula el cupo, cruza solicitudes y ofertas, administra los créditos y contabiliza el reparto entre comercio, prestamista y Trust. Un servicio Go ejecuta las operaciones Open Payments.

La entrada QR Puntored/Bre-B implementada es un simulador. El uso del cliente Open Payments por HTTP puede conectarse a wallets externas, pero no convierte esa entrada en una integración bancaria productiva.

La [documentación técnica](docs/README.md) describe la implementación, sus flujos, contratos, configuración y límites operativos.

| Componente | Tecnología | Guía |
| --- | --- | --- |
| Aplicación web | HTML, CSS y JavaScript con módulos ES | [Frontend](frontend/README.txt) |
| Landing y lista de espera | Astro y TypeScript | [Landing](landing/README.md) |
| API de negocio | Python 3.12, FastAPI y SQLAlchemy | [Backend](backend/README.md) |
| Servicio de pagos | Go y SDK Open Payments | [Open Payments](open-payments/README.md) |
| Persistencia | PostgreSQL, bases separadas por servicio | [Modelo de datos](docs/tecnica/modelo-de-datos.md) |
| Despliegue integrado | Docker Compose, Nginx y Traefik existente | [Operación](docs/tecnica/operacion.md) |

Para levantar la demo local, sigue [Desarrollo local](docs/tecnica/operacion.md#desarrollo-local). Los contratos compartidos están en [contracts/](contracts/); los comportamientos detallados y criterios de aceptación del backend, en [backend/specs/](backend/specs/README.md).
