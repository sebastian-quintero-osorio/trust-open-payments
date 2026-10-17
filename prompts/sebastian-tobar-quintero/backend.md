1. Claude, revisa el archivo de docs del backend en donde se especifican los módulos del monolito en FastAPI y crea una carpeta de specs dentro de backend, construiremos con spec driven, pero lo haremos de una forma muy simple para iterar rápido y sacar el backend completo rápido, tenemos que ser ágiles.

2. Primero que nada, podrías crear un .yaml como el del servicio de Go pero para el backend para pasarselo al equipo de frontend y que vaya integrando trabajo? Ten en cuenta que el equipo de Go ya arrancó a trabajar, así que no hay que cambiarles el contrato, y debemos asegurarnos que el del backend será 100% real para que no haya que cambiar cosas después, revisa e implementa.

3. /loop Eres el implementador del backend Trust (FastAPI) en modo loop. Cada iteración acercas el backend a "100 % implementado, probado y funcionando", sin dejar nada por fuera: todas las specs, todos los endpoints (con ★ y sin ★) y todos los criterios de aceptación. Trabaja solo, sin preguntarme nada (puedo estar dormido): decide con los valores por defecto de las specs y anota cada decisión.

CONTEXTO FIJO
- Repo: /Users/bassee/Desktop/Projects/OpenPayments/trust-open-payments, rama greenfield/backend. Código en backend/.
- Fuentes de verdad: backend/specs/ (README, 00-core, 01-data-model y una spec poriepenapi.yaml para rutas, campos y códigos (el frontend ya lo usa);contracts/op-service.openapi.yaml para hablar con el servicio Go (lo hace otro equipo: NUNCA lo modifiques).                                                                  - Todavía no      existen el servicio Go ni el frontend: todo lo de Go se simula con FaOP_CLIENT=fake.
- Asset de mercado: COP con escala s). Las wallets de recaudo son de Trust y tienen un grant permanente: las ventas se dividen sin consentimiento de nadie. El único link de autorización es el del desembolso.
- Tienes autorización total en local: instala lo que falte (brew, uv), levanta Postgres en Docker (o con brew si no hay Docker), crea bases, corre servidores, haz commits, merge y push.

ESTADO ENTRE ITERACIONES
- backend/LOOP.md es tu memoria. Guarda: entorno (cómo levantar la DB y correr teson contrato, bloqueos y un log por iteración. Léeloprimero; créalo si no existe.Actualízalo antes de cada commit.
- El estado de cada spec está en su cabecera: ☐ pendiente → ◐ en curso → ☑ hecho (

CADA ITERACIÓN
1. Orientación rápida: `git status`, confirmar la rama, leer LOOP.md. Si quedó trabajo a medias, termínalo; nunca descartes cambios sin entenderlos.
2. Toma la primera spec, en el orden del README, que no esté ☑ (si hay una ◐, esa) i specs pequeñas.
3. Imple:
   a) Implementador: lee README, 00-core, 01-data-model, la spec y las secciones dEspor cada AC (`test_acN_...`), con `assert_contract` encada endpoint. Corre pytestcompleto hasta que pase.
   b) Revisor adversarial: compara el código contra la spec y contra el YAML (rutacaror). Busca ACs con tests débiles, dinero con floats,llamadas a Go dentro detransacciones y reglas de la spec sin implementar. Corre pytest y devuelve fallas concretas.
   c) Si hubo fallas, un fixer las corrige y vuelve a correr pytest.
   Mientras el workflow corre no hagas polling: programa solo el despertador de respaldo largo.
4. Verifica tú: pytest completo en verde (incluidas todas las specs anteriores) y t  de esta spec (por ejemplo, ACs de la 03 que esperan a06, 07, 09 u 11), quítale elskip y hazlo pasar.
5. Marca la spec ☑, actualiza LOOP.md y haz UN commit atómico: "Implementar spec Npoal final:
   Co-Authore Opus 5.5 <noreply@anthropic.com>
   Claude-Session: https://claude.ai/code/session_01PAVJYMSHPqvY1AxEqB9eEh
   No hagas push todavía (solo en la fase final). No toques landing/, .internal/, CLAUDE.md ni contracts/op-service.openapi.yaml. No commitees .env ni secretos.
6. Programa la siguiente iteración (60 s si sigues de inmediato).

REGLAS
- La spec manda. Si algo es ambiguo o contradictorio, corrige la spec lo mínimo, en el mismo commit, y anótalo en LOOP.md bajo "Decisiones".
- contracts/trust-api.openapi.yaml solo se cambia si es inevitable: corrige spec ynp0 lint` y anótalo en LOOP.md bajo "CAMBIOS DE CONTRATO"(el frontend ya lo estáusando).
- Dinero: enteros y bps, nunca floats. Nunca llames a op-service con una transacci
- Tests contra Postgres real, nunca SQLite. Cada AC verifica exactamente lo que dice.
- Si algo te bloquea, anótalo en LOOP.md, sigue con la siguiente spec y vuelve a rl queo abierto, ningún test con skip ni ningún AC siar.

FASE FINAL (cuando las 17 specs estén ☑ y no quede ningún skip)
1. Smoke E2E en `backend/scripts/smoke_e2e.py`. Levanta la API con OP_CLIENT=fake ue y recorre la demo, validando cada respuesta contra el YAML:
   seed → login de Marta y Camilo → cuenta de recaudo → venta simulada de $48.000 quud y ofertas → preview → crear el crédito → desembolso → aprobar en la wallet
falsa → el worker lo activa → más ventas → abonos, dashboards y notificaciones.
2. Mock HTTP de op-service en `backend/tools/mock_op_service/` (FastAPI en memoria, .openapi.yaml). Repite la prueba con OP_CLIENT=http contra él, para probar
HttpOpClient y el contrato con Go antes de que Go exista.
3. Despliegue: Dockerfile, docker-compose (trust-api + postgres; caddy opcional), .eE.md con cómo correr todo: tests, seed y demo.
4. Revisión final adversarial (con un workflow) de todo el backend contra las speclo que salga y vuelve a coy la prueba con el mockhasta que todo pase.
5. Commit de cierre en greenfield/backend con el resumen en LOOP.md.
6. Integración a main, sin perder trabajo de nadie:
   a) `git fetch origin` y `git push -u origin greenfield/backend`.
   b) `git checkout main` y `git merge origin/main`, por si otros equipos subieron cambios. Resuelve los conflictos conservando el trabajo de ambos lados.
   c) `git merge --no-ff greenfield/backend`. Resuelve los conflictos de la misma prna los dos lados y nunca borres lo de la landing.
   d) Corre pytest, ruff y el smoke E2E sobre main. Si algo falla, arréglalo en main con un commit.                                                                                                e) `git push origin main`. Si el push es rechazado porque alguien subió algo mi) y reescribas historia ya publicada.
7. Cuando main esté pusheado y en verde, DETÉN el loop (ScheduleWakeup con stop). En el mensaje final dime qué quedó, cómo corre commits en main, las desviaciones de spec o contrato y cualquier cosa que el frontend o el equipo de Go deban saber.