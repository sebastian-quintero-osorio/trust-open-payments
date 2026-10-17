## Fase 2 — Cliente Open Payments
- [x] T06 — Resolver wallet addresses
  Alcance: Consultar wallet addresses y obtener asset, authServer y resourceServer.
  Aceptación: Las wallets reales se resuelven correctamente.
- [x] T07 — Firmas y cliente autenticado
  Alcance: Integrar SDK Go, claves Ed25519 y autenticación del cliente Trust.
  Aceptación: La Test Wallet acepta peticiones firmadas.
- [x] T08 — Grants no interactivos
  Alcance: Solicitar permisos GNAP para incoming payments y quotes.
  Aceptación: Se obtienen tokens con los permisos necesarios.
- [x] T09 — Incoming payments
  Alcance: Implementar Create, Get y Complete, montos exactos y expiración.
  Aceptación: Se crea y consulta un incoming real.
- [x] T10 — Quotes
  Alcance: Implementar Create/Get para fixed-send y fixed-receive.
  Aceptación: Se reciben montos y vencimientos verificables.
- [x] T11 — Outgoing payments
  Alcance: Implementar Create/Get utilizando grants de salida autorizados.
  Aceptación: Se crea un outgoing válido y se consulta su estado.

## Implementación y validación local — 2026-10-17

T06–T11 implementadas en `internal/openpayments`, con SDK oficial v0.5.0 para
firmas Ed25519 y adaptadores que conservan URLs opacas y montos exactos.
Pruebas HTTPS locales verifican firma, tenant, grants, incoming Create/Get/Complete,
quote Create/Get, outgoing Create/Get, SSRF, redirects y ausencia de reintentos POST.
`scripts/check.sh` pasó con PostgreSQL, race, lint, seguridad, OpenAPI y secretos.

Por instrucción del usuario, configurar wallets/credenciales y ejecutar los criterios
de aceptación contra Test Wallet se difiere al cierre final. Los checkboxes indican
implementación y validación local; no certifican aceptación en la wallet real.
Sin push por instrucción del usuario.
