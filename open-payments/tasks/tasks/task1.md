## Fase 1 — Base del servicio y DevSecOps
- [x] T01 — Inicializar proyecto Go
  Alcance: Configurar estructura modular, servidor HTTP y /v1/health.
  Aceptación: Servidor inicia y responde health.
- [x] T02 — Configuración segura
  Alcance: Implementar variables de entorno, validación, manejo de secretos y shutdown.
  Aceptación: Config inválida falla de forma segura; no aparecen secretos en logs.
- [x] T03 — Contrato y seguridad HTTP
  Alcance: Implementar modelos OpenAPI, validaciones, errores y X-Internal-Api-Key.
  Aceptación: Endpoints cumplen el contrato y rechazan peticiones inválidas.
- [x] T04 — Base de datos e idempotencia
  Alcance: Crear migraciones PostgreSQL, repositorios y control de Idempotency-Key.
  Aceptación: Requests duplicados no generan operaciones duplicadas.
- [x] T05 — Pipeline DevSecOps
  Alcance: Automatizar lint, go vet, race tests, gosec, govulncheck y escaneo de secretos.
  Aceptación: Los PR quedan bloqueados ante fallos críticos.

## Cierre local — 2026-10-17

El usuario autorizó realizar únicamente commits, sin push. Esta fase se cierra
localmente bajo esa instrucción; publicación y protección de rama quedan diferidas.

- T01: módulo Go 1.26.9, servidor HTTP, health y prueba del proceso real.
- T02: configuración validada, Ed25519 PKCS#8, redacción de secretos en formato
  texto/JSON/slog, timeouts y cierre mediante SIGTERM probado.
- T03: contrato original embebido, generación reproducible de modelos, JSON Schema,
  API key en tiempo constante, límites de cuerpo, rechazo de JSON ambiguo y errores.
- T04: migraciones PostgreSQL con checksum y lock, repositorios transaccionales,
  idempotencia de 72 h, replay con status original y rollback de mutaciones ante errores.
  Probados conflictos, concurrencia, expiración, reapertura y fallos transitorios.
- T05: pipeline y script compartido, analizadores fijados por versión y acciones por
  SHA. Los fallos hacen fallar el check; exigirlo para bloquear merges requiere
  configurar protección remota cuando el usuario habilite la publicación.

Validación final: `scripts/check.sh` terminó con código 0 usando PostgreSQL 17.6
local aislado. Pasaron gofmt, regeneración, go mod verify, go vet, Staticcheck,
`go test -race -count=1 ./...`, gosec (0 issues, 0 supresiones), govulncheck,
validación OpenAPI 3.1 y Gitleaks sobre archivos e historial. Se corrigieron dos
vulnerabilidades transitivas actualizando golang.org/x/text a v0.41.0.
Go 1.26.9 evita la incompatibilidad del importador de gosec con Go 1.27.

Los endpoints financieros aún responden 503 y no crean operaciones: su
implementación pertenece a las fases 2–5. No se declara validada la Test Wallet.
