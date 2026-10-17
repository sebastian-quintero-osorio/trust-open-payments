# Capturas de la app: slots y cómo reemplazar los mocks

La landing muestra cinco pantallas de la app de Trust dentro de `<DeviceFrame>` (`src/components/ui/DeviceFrame.astro`). Mientras no haya capturas reales, cada slot muestra un mock en HTML/CSS (`src/components/screens/*Screen.astro`) construido según `docs/COPY.md` §13. Cuando llegue una captura, se pasa como `screenshot` al mismo `<DeviceFrame>` y el mock deja de renderizarse.

- Galería de los cinco mocks (solo en desarrollo): `http://localhost:4321/preview/screens`
- En desarrollo, cada teléfono sin captura muestra la etiqueta amarilla "Captura pendiente · `<screenId>`". Desaparece cuando el slot recibe `screenshot`.
- La animación de los mocks (`src/components/screens/screen-anim.ts`: conteos y transiciones al entrar en pantalla) se importa una sola vez, desde la rama del mock de `DeviceFrame`; las pantallas no tienen `<script>` propio. Mientras al menos un teléfono muestre un mock, la página carga ese script una vez; cuando los cinco reciben `screenshot`, deja de cargarse.
- Las cinco pantallas cuentan una sola historia con los mismos datos de ejemplo (COPY.md §13.0): Tienda La Esquina (Marta R., Bucaramanga), prestamista Camilo P., cupo $6.000.000, crédito activo de $2.000.000, división 85 % / 15 %, venta de $48.000 → $40.800 + $7.200, hoy 14 ventas por $612.000, viernes 16 oct 2026.


> **Estado actual de la landing:** se muestran 3 pantallas: `cupo-dashboard` (hero), `solicitud-credito` (Comercios, anotada) y `portafolio-prestamista` (Prestamistas). `division-en-vivo` y `detalle-match` siguen construidas, pero no aparecen en la página: el diagrama y el cruce de rangos ya cuentan esa historia.

## Resumen

| screenId | Componente mock | Sección de la landing | Archivo de la captura |
| --- | --- | --- | --- |
| `cupo-dashboard` | `src/components/screens/CupoScreen.astro` | Hero (`src/components/sections/Hero.astro`), teléfono principal | `src/assets/screens/cupo-dashboard.png` |
| `solicitud-credito` | `src/components/screens/SolicitudScreen.astro` | Comercios (`src/components/sections/Comercios.astro`) | `src/assets/screens/solicitud-credito.png` |
| `division-en-vivo` | `src/components/screens/SplitFeedScreen.astro` | Cómo funciona (`src/components/sections/ComoFunciona.astro`) | `src/assets/screens/division-en-vivo.png` |
| `portafolio-prestamista` | `src/components/screens/PortafolioScreen.astro` | Prestamistas (`src/components/sections/Prestamistas.astro`) | `src/assets/screens/portafolio-prestamista.png` |
| `detalle-match` | `src/components/screens/MatchScreen.astro` | El match (`src/components/sections/Match.astro`) | `src/assets/screens/detalle-match.png` |

## Formato de todas las capturas

- **Dispositivo:** iPhone en vertical (familia 6,1" con Dynamic Island, lienzo de 390 × 844 pt).
- **Exportación:** PNG de **1170 × 2532 px** (@3x). Mínimo aceptado: **780 × 1688 px** (@2x). Misma proporción 390:844; no recortar ni cambiar la proporción.
- **Solo la pantalla**, rectangular y sin marco: `DeviceFrame` dibuja el marco, las esquinas redondeadas y la Dynamic Island encima.
- **Modo oscuro** de la app (fondo #191919, superficies #1F1D25).
- **Barra de estado limpia:** hora 9:41, señal y wifi completos, batería llena, sin notificaciones ni indicadores extra (usar el modo de capturas del simulador o `xcrun simctl status_bar booted override --time "9:41" --batteryLevel 100 --batteryState charged --cellularBars 4 --wifiBars 3`).
- **Idioma:** español de Colombia, con los formatos de COPY.md: `$48.000`, `15 %`, `10:42 a. m.`, `16 oct 2026`.
- **Datos:** exactamente los de ejemplo de COPY.md §13 (los mismos del mock). No mostrar tasas, costos, rendimientos, nombres de bancos ni logos de terceros (COPY.md `screens.no_mostrar`).
- **Optimización:** no hace falta exportar AVIF/WebP a mano. `DeviceFrame` usa `<Image>` de `astro:assets`, que genera las variantes (`widths` 320–1170) en el build. Por eso el archivo va en `src/assets/screens/` y no en `public/` (esto reemplaza la ruta `public/screens/<id>.avif` que menciona COPY.md §13.0).

## Cómo cambiar un mock por la captura

1. Guarda el PNG como `src/assets/screens/<screenId>.png`.
2. En la sección correspondiente, importa la imagen y pásala al `<DeviceFrame>` que ya existe con `screenshot={...}`. Con `screenshot`, el contenido del slot (el mock) se ignora.
3. Opcional, cuando las cinco capturas estén listas: borra el hijo `<XxxScreen />` y su import. Los archivos de `src/components/screens/` pueden quedarse como respaldo.
4. Revisa en `http://localhost:4321/preview/<sección>` que la etiqueta "Captura pendiente" desapareció y que la imagen se ve nítida (ajusta `sizes` al `width` del frame si hace falta).

Ejemplo (Hero):

```astro
---
import DeviceFrame from '../ui/DeviceFrame.astro';
import CupoScreen from '../screens/CupoScreen.astro';
import cupoDashboard from '../../assets/screens/cupo-dashboard.png';
---

<DeviceFrame
  screenId="cupo-dashboard"
  alt={copy.mockupAlt}
  screenshot={cupoDashboard}
  width="clamp(220px, min(23vw, calc((100svh - 250px) * 0.45)), 330px)"
  priority
>
  <CupoScreen /> <!-- ignorado mientras exista `screenshot`; se puede borrar -->
</DeviceFrame>
```

El pie **"Pantalla ilustrativa con datos de ejemplo."** (COPY.md `screens.caption`) se mantiene bajo cada teléfono mientras los datos de la captura sean de ejemplo. En la sección Match el pie es `match.caption` ("Datos de ejemplo."). Solo se retira si la captura muestra datos reales y el equipo legal lo aprueba. Recuerda que todo porcentaje de división visible debe llevar "Ejemplo" o ese pie (COPY.md `conv.ejemplo`).

---

## 1. `cupo-dashboard`: "Tu cupo"

- **Componente mock:** `src/components/screens/CupoScreen.astro`
- **Sección:** Hero, teléfono principal con los tres chips flotando encima. Carga prioritaria (`priority`), es la única captura que no se carga diferida.
- **Alt (COPY.md):** App de Trust: Tienda La Esquina tiene $4.000.000 de cupo disponible y un crédito activo de $2.000.000 pagado en un 32 %.
- **La captura debe mostrar (COPY.md §13a):**
  - Barra de estado 9:41.
  - Encabezado: "Hola, Marta", avatar con iniciales "TL", "Tienda La Esquina · Bucaramanga", ícono de notificaciones con punto.
  - Tarjeta principal: "Cupo disponible" **$4.000.000** y "Cupo total $6.000.000", con el botón principal **"Pedir crédito"**.
  - Tarjeta "Crédito activo": $2.000.000, "15 % de cada venta", "Prestamista: Camilo P.", barra de avance "Pagado $640.000 · 32 %", "Desde 2 oct 2026".
  - Tarjeta "Ventas de hoy con Bre-B": $612.000, "14 ventas", "Para ti $520.200", "Abonado $91.800".
  - Accesos: "Ver ventas", "Ofertas para ti (3)", "Historial".
  - Barra inferior: Inicio (activo), Ventas, Créditos, Perfil.
- **Archivo:** `src/assets/screens/cupo-dashboard.png` · import en `src/components/sections/Hero.astro`.

## 2. `solicitud-credito`: "Solicitar crédito"

- **Componente mock:** `src/components/screens/SolicitudScreen.astro`
- **Sección:** Comercios, junto a "Tú eliges el rango" y "Tu cupo, claro".
- **Alt (COPY.md):** App de Trust: solicitud de crédito con un rango de $1.500.000 a $3.000.000 y un abono del 15 % de cada venta.
- **La captura debe mostrar (COPY.md §13b):**
  - Flecha atrás y "Paso 2 de 3 · Monto".
  - Recordatorio "Tu cupo: $6.000.000".
  - Selector de rango de dos puntos: mínimo **$1.500.000**, máximo **$3.000.000**, escala de $500.000 a $6.000.000, tramo elegido con relleno morado.
  - Selector "Porcentaje de tus ventas para abonar": 10 % · **15 %** (seleccionado) · 20 %.
  - Vista previa: "Con 15 %, una venta de $48.000 abona $7.200 y te quedan $40.800."
  - Aviso: "Las condiciones finales se acuerdan con el prestamista antes de aceptar."
  - Contador: "3 ofertas encajan con tu rango".
  - Botón principal **"Publicar solicitud"** y acción secundaria "Ver ofertas que encajan".
- **Archivo:** `src/assets/screens/solicitud-credito.png` · import en `src/components/sections/Comercios.astro`.

## 3. `division-en-vivo`: "Ventas de hoy"

- **Componente mock:** `src/components/screens/SplitFeedScreen.astro`
- **Sección:** Cómo funciona, al lado del diagrama animado.
- **Alt (COPY.md):** App de Trust: ventas del día divididas en vivo, 85 % para el comercio y 15 % para su crédito.
- **La captura debe mostrar (COPY.md §13c):**
  - Fecha "Viernes, 16 oct 2026" e indicador "En vivo".
  - Resumen: "Ventas $612.000", "Para ti $520.200", "Abonado a tu crédito $91.800", "14 ventas".
  - Regla fija sobre la lista: "85 % para ti · 15 % a tu crédito".
  - Movimientos, cada uno con su barra dividida 85/15:
    - 10:42 a. m. · Venta Bre-B **$48.000** → Para ti $40.800 · Crédito $7.200
    - 10:31 a. m. · Venta Bre-B $26.000 → Para ti $22.100 · Crédito $3.900
    - 10:05 a. m. · Venta Bre-B $12.500 → Para ti $10.625 · Crédito $1.875
    - 9:47 a. m. · Venta Bre-B $85.000 → Para ti $72.250 · Crédito $12.750
  - Enlace "Ver las 14 ventas" y pie "En tu wallet Puntored hoy: $520.200".
  - Botón principal **"Ver detalle del crédito"**.
- **Archivo:** `src/assets/screens/division-en-vivo.png` · import en `src/components/sections/ComoFunciona.astro`.

## 4. `portafolio-prestamista`: "Mi portafolio"

- **Componente mock:** `src/components/screens/PortafolioScreen.astro`
- **Sección:** Prestamistas.
- **Alt (COPY.md):** App de Trust: portafolio de un prestamista con tres créditos activos y los abonos que llegan con cada venta.
- **La captura debe mostrar (COPY.md §13d):**
  - Encabezado: "Hola, Camilo" · "3 créditos activos".
  - Tarjeta principal: "Total prestado" **$5.500.000** · "Abonos de hoy" **+$186.400**.
  - Chip "Mi rango: $1.000.000 – $2.500.000" con ícono de editar.
  - Gráfico "Abonos de hoy por hora": 8:00 a. m. $38.100 · 9:00 a. m. $71.500 · 10:00 a. m. $76.800.
  - Créditos:
    - Tienda La Esquina · $2.000.000 · 15 % de cada venta · Pagado 32 % · hoy +$91.800
    - Panadería El Trigal · $1.500.000 · 12 % de cada venta · Pagado 58 % · hoy +$58.200
    - Papelería Nova · $2.000.000 · 10 % de cada venta · Pagado 12 % · hoy +$36.400
  - "Últimos abonos": 10:42 a. m. Tienda La Esquina +$7.200 · 10:31 a. m. Tienda La Esquina +$3.900 · 10:15 a. m. Panadería El Trigal +$4.500.
  - Nota: "Cada abono es trazable hasta la venta que lo originó."
  - Botón principal **"Publicar oferta"** y secundario "Ver solicitudes".
- **Archivo:** `src/assets/screens/portafolio-prestamista.png` · import en `src/components/sections/Prestamistas.astro`.

## 5. `detalle-match`: "Match encontrado"

- **Componente mock:** `src/components/screens/MatchScreen.astro`
- **Sección:** El match, como pantalla final del visual solicitud → oferta → match.
- **Alt (COPY.md):** App de Trust: match entre la solicitud de Tienda La Esquina y la oferta de un prestamista por $2.000.000, con el 15 % de cada venta.
- **La captura debe mostrar (COPY.md §13e):**
  - Sello superior "Match" con glow lavanda.
  - Tarjeta "Tu solicitud": Tienda La Esquina · Rango $1.500.000 – $3.000.000 · Cupo $6.000.000.
  - Tarjeta "Oferta": Camilo P. · Rango $1.000.000 – $2.500.000 · "Oferta recibida: 1 oct 2026".
  - Franja de rangos superpuestos: "Rangos que se cruzan: $1.500.000 – $2.500.000".
  - Propuesta: "Monto $2.000.000" · "15 % de cada venta con Bre-B".
  - Vista previa: "Una venta de $48.000 → $40.800 para ti · $7.200 para Camilo P."
  - Enlace "Ver condiciones completas" (sin cifras de tasa ni costo).
  - Nota: "Al aceptar, el porcentaje acordado se aplicará a tus ventas cobradas con Bre-B a través de Puntored."
  - Botón principal **"Aceptar oferta"** y secundario "Ver otras ofertas".
- **Archivo:** `src/assets/screens/detalle-match.png` · import en `src/components/sections/Match.astro`.

---

## Notas sobre los mocks (mientras no hay capturas)

- Son decorativos: `DeviceFrame` les pone `role="img"` con el `alt`, y no tienen elementos enfocables.
- Animación (`src/components/screens/screen-anim.ts` + `screen-kit.css`): cuando el teléfono entra en pantalla, los montos clave cuentan hasta su valor una vez, las barras y rangos se llenan (`transform: scaleX`), la primera venta entra en el feed y el sello del match brilla. Los bucles (punto "En vivo", destellos, sello) solo corren mientras el teléfono es visible. Con `prefers-reduced-motion` o sin JavaScript se ve directamente el estado final.
