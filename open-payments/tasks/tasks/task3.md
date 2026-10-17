## Fase 3 — Autorización y persistencia
- [x] T12 — Grants interactivos
  Alcance: Solicitar permisos de outgoing con límites y redirect.
  Aceptación: La wallet presenta el consentimiento correcto.
- [x] T13 — Callback y confirmación
  Alcance: Validar hash, nonce, interact_ref y ejecutar Grant.Continue.
  Aceptación: Aprobaciones y rechazos se procesan una sola vez.
- [x] T14 — Rotación y revocación
  Alcance: Persistir tokens cifrados, serializar rotación y revocar grants.
  Aceptación: Un token vencido puede renovarse correctamente.
- [x] T15 — Máquinas de estados
  Alcance: Implementar estados de payments, transfers, grants y recuperación de procesos.
  Aceptación: Estados terminales no cambian y se recuperan tareas tras reinicios.

## Implementación y validación local — 2026-10-17

T12–T15 implementadas: interacción con límite debitAmount, verificación SHA-256
con ambos formatos base64, callback consumido transaccionalmente, rotación y
revocación sobre URLs opacas, AES-256-GCM con HKDF desde la clave Ed25519,
serialización PostgreSQL por grant, estados terminales y cola durable con leases.
Pruebas verifican rechazo de hashes inválidos sin consumir el attempt, callbacks
repetidos, manipulación de ciphertext, rotación de tokens vencidos con token/manage
nuevos atómicos, recuperación de leases y rechazo de workers antiguos.

`scripts/check.sh` pasó completo. Aceptación contra Test Wallet diferida por
instrucción del usuario; sin push. La integración de estas piezas con los endpoints
y el worker financiero corresponde a la fase 4.
