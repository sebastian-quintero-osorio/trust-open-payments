## Fase 4 — Flujos financieros de Trust
- [x] T16 — Registro de wallets de recaudo
  Alcance: Asociar wallets de comercios y preparar grants para salidas autorizadas.
  Aceptación: Trust identifica la wallet asignada y sus permisos.
- [x] T17 — Desembolsos
  Alcance: Implementar POST/GET payments y secuencia incoming → quote → grant → outgoing.
  Aceptación: Un prestamista autoriza y un comercio recibe el crédito.
- [x] T18 — Transferencias de distribución
  Alcance: Implementar POST/GET transfers, workers por grant y fixed-send.
  Aceptación: Las transferencias se ejecutan y reportan sin duplicados.
- [x] T19 — Conciliación de recaudos
  Alcance: Verificar capacidades de notificación o consulta, correlacionar ventas y emitir eventos a Trust.
  Aceptación: Una venta de prueba queda identificada y vinculada a su comercio sin duplicados.

## Implementación y validación local — 2026-10-17

T16–T19 implementadas y enlazadas al servidor: bootstrap del pool, autorización,
asignación transaccional, payments, transfers, simulador, QR público, conciliación
y outbox firmado con bytes estables. Los workers serializan por grant y no esperan
la liquidación de una salida para iniciar la siguiente. Un POST de outgoing incierto
se reconcilia por metadata.op_operation_id; nunca se repite a ciegas.

La prueba HTTPS/PostgreSQL recorre crédito → venta QR → webhook → distribución
entre tres receptores, comprueba contratos, saldo informativo, Incoming.Complete,
falta de saldo, pérdida de respuesta sin doble pago, callbacks repetidos,
concurrencia de asignación y doble envío del formulario. `scripts/check.sh` pasó
completo, incluidos race y todos los escáneres. Credenciales y pruebas reales se
reservan para el final conforme a la instrucción del usuario. Commit local sin push.
