# Estándares de desarrollo — op-service

## 1. Principios de ingeniería

- Priorizar correctitud, seguridad, simplicidad y mantenibilidad.
- Seguir las convenciones idiomáticas de Go.
- Aplicar SOLID, DRY, KISS y separación de responsabilidades con criterio, evitando abstracciones innecesarias.
- Usar nombres descriptivos en inglés para funciones, variables, tipos y paquetes.
- Escribir funciones pequeñas, cohesivas y fáciles de probar.
- Documentar las funciones exportadas y las decisiones técnicas no evidentes. Los comentarios deben explicar el *porqué*, no repetir el código.
- Manejar errores explícitamente, propagando contexto y sin ignorar fallos.
- Utilizar `context.Context` y timeouts en operaciones externas.
- Mantener independientes las capas HTTP, dominio, integración Open Payments y persistencia.

## 2. Contratos y documentación

- `AGENTS.md` define el contexto y las responsabilidades del proyecto.
- `op-service.openapi.yaml` es la fuente de verdad del contrato HTTP con Trust API.
- Consultar la documentación oficial de https://openpayments.dev/ antes de implementar operaciones del protocolo.
- Verificar las interfaces actuales del SDK de Go y el comportamiento de Interledger Test Wallet.
- No inventar funcionalidades ni modificar unilateralmente el contrato OpenAPI.
- Actualizar la documentación afectada por cada cambio.

## 3. Seguridad y consistencia financiera

- Nunca exponer secretos, claves privadas, access tokens o manage URLs.
- Validar estrictamente entradas, URLs, permisos, assets y montos.
- Utilizar enteros en unidades mínimas para representar dinero; nunca floats.
- Respetar el ciclo de vida de grants GNAP, consentimiento, tokens y recursos Open Payments.
- Implementar idempotencia, transacciones y control de concurrencia.
- No reintentar pagos automáticamente cuando exista incertidumbre sobre su ejecución.
- Confirmar el resultado real antes de marcar una operación como completada.
- Mantener transiciones de estado válidas, trazabilidad y mecanismos de recuperación ante fallos.
- Aplicar mínimo privilegio, protección contra SSRF y manejo seguro de credenciales.

## 4. DevSecOps y calidad

Cada tarea debe incluir las pruebas y validaciones aplicables:

- Formato y análisis: `gofmt`, `go vet` y lint.
- Pruebas unitarias, de integración y de casos límite.
- Detección de condiciones de carrera: `go test -race ./...`.
- Seguridad: `gosec`, `govulncheck` y escaneo de secretos.
- Validación del contrato OpenAPI cuando corresponda.
- Pruebas reales contra Interledger Test Wallet en las fases de integración.

No omitir pruebas ni desactivar controles para hacer pasar una implementación.

## 5. Ejecución secuencial de fases

Las fases están definidas en los archivos `task1.md` a `task5.md` de esta carpeta y deben ejecutarse en orden numérico.

Para cada fase:

1. Leer el archivo de tareas y sus dependencias.
2. Revisar el código y la documentación relevante.
3. Implementar las tareas en orden, sin ampliar innecesariamente su alcance.
4. Escribir y ejecutar las pruebas correspondientes.
5. Resolver los errores detectados y ejecutar nuevamente las validaciones.
6. Verificar los criterios de aceptación de todas las tareas.
7. Actualizar la documentación y marcar las tareas completadas en el archivo de la fase.
8. Realizar commit y push siguiendo las reglas de Git.
9. Continuar con la siguiente fase.

No declarar completada una tarea o fase sin evidencia de las validaciones requeridas.

## 6. Git y control de versiones

Al completar correctamente cada fase:

1. Identificar la rama actual con `git branch --show-current`.
2. Revisar cambios mediante `git status` y `git diff`.
3. Verificar que no existan secretos ni archivos temporales entre los cambios.
4. Realizar un commit descriptivo siguiendo Conventional Commits.
5. Hacer push a la misma rama, sin crear ni cambiar de rama.
6. Verificar que el push se realizó correctamente.
7. Continuar con la siguiente fase.

Ejemplo de commit:

`feat(op-service): implement Open Payments grant management`

No realizar commits de fases incompletas ni hacer push con validaciones obligatorias fallidas.

No ejecutar `git push --force`, `git reset --hard` ni reescribir historial.

Si falla el commit o el push, informar el problema y no considerar cerrada la fase. No sobrescribir cambios de otros colaboradores.

## 7. Autonomía y reporte

El agente está autorizado a ejecutar todas las fases secuencialmente, sin solicitar confirmación entre ellas, siempre que cumpla los criterios establecidos.

Debe detenerse ante bloqueos que requieran credenciales inexistentes, cambios contractuales no aprobados, decisiones de negocio o riesgos de seguridad que no pueda resolver.

Al finalizar cada fase, presentar brevemente:

- Fase y tareas completadas.
- Implementación realizada.
- Pruebas y controles de seguridad ejecutados.
- Problemas encontrados y decisiones tomadas.
- Commit generado, rama y resultado del push.
- Estado: completada o bloqueada.

**Una fase termina únicamente cuando su implementación, validación y publicación en la rama actual han finalizado correctamente.**