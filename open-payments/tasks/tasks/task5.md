## Fase 5 — Integración y demostración
- [ ] T20 — Pruebas de integración y fallos
  Alcance: Validar API Go/FastAPI, expiraciones, falta de saldo, concurrencia y red caída.
  Aceptación: Los fallos son trazables y no desencadenan dobles pagos.
- [ ] T21 — Despliegue y demo final
  Alcance: Docker, configuración segura, test end-to-end y documentación.
  Aceptación: Crédito → venta con QR → recaudo confirmado → distribución.

### Evidencia local — 2026-10-17

- [x] Suite Go con PostgreSQL real, receptor FastAPI y detector de carreras.
- [x] Fallos de consentimiento, saldo insuficiente, concurrencia, revocación,
  pérdida de respuesta, reinicio y reintentos de webhook sin duplicar pagos.
- [x] gofmt, go vet, staticcheck, gosec (0 incidencias), govulncheck,
  contrato OpenAPI y Gitleaks sobre archivos publicables e historial.
- [x] Imagen Docker distroless no root construida; restricciones de ejecución y
  healthcheck verificados; Trivy sin hallazgos HIGH/CRITICAL.
- [x] Configuración Caddy de producción y demo validada.
- [x] Demo sin dominio: túnel temporal, retorno predeterminado y API en localhost;
  pruebas del arranque del túnel y Compose válido.
- [ ] Aceptación real en Test Wallet y backend de negocio Trust.

Bloqueos actuales para la prueba real: OP_PRIVATE_KEY no es PEM PKCS#8 válido;
OP_CLIENT_WALLET_ADDRESS y POOL_WALLET_ADDRESSES no tienen formato URL HTTPS
válido; el daemon Docker del sistema no está iniciado. SIM_ENABLED=false impide
las ventas QR hasta configurar y activar el simulador. Las validaciones Docker
locales se ejecutaron con un daemon aislado de pruebas. No se modificó .env.

T20/T21 permanecen sin cerrar hasta completar la aceptación real. El usuario
autorizó publicar el avance validado localmente, incluida la plantilla .env.example;
el commit y el push de este avance no equivalen a aprobar la demo financiera real.
