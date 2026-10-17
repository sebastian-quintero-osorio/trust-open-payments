# Trust · Sistema visual de la landing

Dirección **Órbita** (definida por el equipo de frontend de la plataforma) aplicada a la landing. Este documento manda sobre cualquier decisión de diseño. Antes de agregar o cambiar una sección, revisa la lista de patrones prohibidos.

## 1. Principios Órbita

- **Profundidad, luz ambiental y superficies oscuras.** La sensación es tecnológica, elegante y ligeramente futurista.
- **Color**:
  - Negro (`#191919`) como base dominante.
  - Morado eléctrico (`#6930F9`) para **acciones y focos visuales**.
  - Lavanda (`#B499FE`) y lila (`#F1E9FD`) para contraste y luminosidad.
  - La proporción 65/25/10 orienta; no es una cuota por sección.
- **El arco orbital vive en el hero.** Es el elemento distintivo de la marca: horizonte luminoso, gradientes suaves, brillo controlado. No se repite en las secciones intermedias (ni bordes curvos, ni planetas, ni anillos concéntricos). La única excepción, decidida por el cliente, es el cierre: en el footer el logotipo sube detrás de una línea de horizonte con su resplandor, como contraparte del hero.
- **Un protagonista por sección.** Cada sección tiene un único elemento visual principal. Todo lo demás es texto y espacio.
- **Vidrio en dos elementos como máximo**: la barra de navegación y la tarjeta flotante del hero. El contenido de lectura va sobre superficies sólidas.
- **Bordes sutilmente iluminados, radios generosos, sombras suaves.**
- **Espacio negativo amplio** y jerarquía tipográfica clara.

## 2. Lo que hace única a Trust: el producto

La landing muestra la plataforma, no decoración. Los protagonistas son el mecanismo y la interfaz real:

| Sección | Protagonista |
| --- | --- |
| Hero | Arco orbital + teléfono con el cupo + una tarjeta que divide una venta real del ejemplo |
| El problema | Una cifra con fuente (666,5 %), tratada como dato, no como adorno |
| Cómo funciona | El diagrama animado de la división 85/15 |
| Comercios | La pantalla de solicitud **anotada**: cada beneficio señala la parte de la app que lo cumple |
| Prestamistas | El portafolio sobre la sección lila (único fondo claro de la página) |
| Match | Los dos rangos que se cruzan |
| Confianza | El registro de una venta: de la venta Bre-B al abono, con hora y monto |
| Lista de espera | El formulario |
| Footer | El logotipo sobre la línea de horizonte (cierre que responde al hero) |

Un único caso de datos recorre toda la página: venta de **$48.000 → $40.800** para el comercio y **$7.200** para el prestamista (85 % / 15 %, ejemplo). Ver `docs/COPY.md`.

## 3. Patrones prohibidos (y por qué)

Son las señales más citadas de diseño genérico generado por IA. Fuentes: Paralect, *21 patterns of AI slop*; Developers Digest, *16 patterns*; DEV Community, *I catalogued every tell*; guías sobre redacción con IA.

| Prohibido | Por qué | En su lugar |
| --- | --- | --- |
| Pastilla o badge sobre el H1 | La señal más reconocible de una landing generada | Nada. El contexto va en el subtítulo |
| **Testigos**: puntos de estado ("En vivo"), puntos de notificación, viñetas de color en leyendas, nodos decorativos en líneas de tiempo, parpadeen o no | Delatan plantilla de IA aunque estén quietos | El texto dice el estado; el color del propio monto indica de quién es |
| Puntos que pulsan, brillos que barren, elementos que levitan o flotan en bucle | Movimiento decorativo sin significado | El movimiento explica el producto (división, match, conteo de un monto) o no existe |
| Texto con degradado en titulares | Delata la plantilla y además lee peor | Titulares en un solo color. La jerarquía la dan el tamaño y el espacio |
| Eyebrows en mayúsculas con línea ("— EL PROBLEMA") sobre cada H2 | Etiqueta repetida que solo nombra la sección | El H2 se sostiene solo |
| Íconos de línea dentro de cuadritos de color en cada tarjeta o beneficio | Rejilla de "feature cards" genérica | Texto con buena jerarquía, o una anotación sobre la interfaz real |
| Pasos numerados 01 · 02 · 03 | Patrón de plantilla | El diagrama ordena los pasos por posición |
| Fila de estadísticas gigantes con conteo animado | Parece prueba social inventada | Una cifra protagonista, con su fuente al lado |
| Bento de tarjetas de vidrio | Vidrio sobre manchas de color, bajo contraste | Superficies sólidas, divisores finos |
| Bordes alrededor de todo, cajas dentro de cajas | Nada destaca | Divisores de 1 px y espacio |
| El mismo radio y la misma sombra en todo | Todo parece el mismo componente | Escala de radios por rol (ver §5) |
| Flecha en cada botón | Ruido | Flecha solo cuando indica un desplazamiento |
| Ícono "sparkles" (✨) | El ícono de la IA | Nunca |
| Partículas, órbitas de puntitos, rejillas de fondo, neón | Estética gamer o sci-fi, prohibida por la marca | Luz ambiental suave, solo en el hero |
| Escribir "Trust" con una fuente para simular el logo | Rompe la geometría del logotipo | El componente `Logo` (PNG oficial) |
| Copy con antítesis inventada ("X no se promete. Se ve…"), pares de fragmentos ("Dos rangos. Un acuerdo."), tríadas por reflejo, rayas largas (—) | Ritmo típico de texto generado | Frases concretas, con verbo, que solo podría decir Trust |

## 4. Tipografía

- **Qontra** (Tomaz Hrastar) para titulares, cifras y el diagrama. Es la tipografía del manual de marca.
  - Solo existe en versión *Regular* pública, así que la jerarquía sale del tamaño, el color y el interlineado, nunca de negritas falsas.
  - La versión pública no traía `á é í ó ú ñ ¿ ¡ ’ ”`. Se agregaron con `scripts/fonts/patch-qontra.py` a partir de la construcción de la propia fuente.
  - **Licencia por confirmar** con el autor antes del lanzamiento comercial. Los sitios de descarga se contradicen. Si no se confirma, se sustituye temporalmente por **Barlow Semi Condensed** (OFL), que conserva el carácter técnico, y se documenta aquí.
- **Barlow** (OFL; pesos 400 y 600) para texto y controles. Tiene raíz DIN como Qontra, la misma geometría cuadrada del logotipo y buena lectura en pantallas oscuras. Reemplaza a Geist, la fuente por defecto de las herramientas de generación.
- Nada en mayúsculas sostenidas salvo siglas. Cifras tabulares (`.tnum`) en montos que cambian.

## 5. Componentes

- **Botón principal**: morado eléctrico con texto blanco (6,2:1). Radio 12 px, que recoge las esquinas casi rectas del logotipo.
- **Acción secundaria**: enlace de texto en lavanda, subrayado al pasar el cursor.
- **Radios**: controles 12 px · tarjetas 20 px · teléfono según su marco.
- **Superficies**: `--surface-solid` (#1F1D25) para tarjetas de lectura. Vidrio solo en la navegación y en la tarjeta del hero.
- **Divisores**: 1 px, `--border`.
- **Íconos**: solo en controles de interfaz (menú, validación de formulario) y dentro de las pantallas de la app. Nunca como decoración de un texto.

## 6. Movimiento

- **Sí**: la aurora del hero (luz ambiental, se pausa fuera de pantalla), el diagrama de la división, el cruce de rangos, el registro de una venta que se completa, los montos de las pantallas que llegan a su valor, la entrada del titular.
- **No**: bucles decorativos (levitar, flotar, respirar, girar, barrer, pulsar).
- **Reveals al hacer scroll**: solo en los protagonistas visuales, sutiles (opacidad y 16 px). Nunca en cada párrafo.
- **Siempre**:
  - Solo `transform` y `opacity`.
  - Pausa fuera de pantalla.
  - `prefers-reduced-motion` muestra el estado final.
- **Sin botón de pausa** (decisión de producto). Para cumplir con el espíritu de WCAG 2.2.2 sin ese control, lo que se repite se detiene solo: el diagrama de la división hace pocas vueltas y descansa en su estado final, y vuelve a empezar al regresar a la sección. La aurora del hero es luz ambiental lenta y se apaga fuera de pantalla.

## 7. Contraste

`npm run contrast` verifica todos los pares de tokens (WCAG AA). El morado eléctrico nunca lleva texto sobre negro. En la sección lila sí funciona como texto.
