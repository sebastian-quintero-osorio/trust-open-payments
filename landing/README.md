# Trust · Landing comercial

Landing de Trust en [Astro 7](https://docs.astro.build). Estática, sin frameworks de UI y con JS mínimo (vanilla, por componente).

## Desarrollo

```sh
npm install
npm run dev          # http://localhost:4321 con recarga en caliente
npm run build        # genera dist/
npm run preview      # sirve dist/
npm run check        # astro check (tipos)
npm run contrast     # auditoría WCAG de los pares de color de tokens.css
```

Vista de una sola sección en desarrollo: `http://localhost:4321/preview/<id>` (`nav`, `hero`, `problema`, `como-funciona`, `comercios`, `prestamistas`, `match`, `confianza`, `faq`, `cta-final`, `footer`) y `/preview/screens` para las 5 pantallas de la app. Estas rutas no se generan en producción.

## Variables de entorno (opcionales)

Se leen del entorno o de un archivo `.env` en `landing/` (ignorado por git; el valor del entorno gana). `astro.config.mjs` usa `loadEnv` de Vite para que `PUBLIC_SITE_URL` también funcione desde `.env`.

| Variable | Uso |
| --- | --- |
| `PUBLIC_SITE_URL` | Dominio de producción: canonical, `og:url`, `og:image` y JSON-LD. Sin ella se usa `https://trust.example.com` y `npm run build` muestra un aviso (no falla). |
| `PUBLIC_WAITLIST_ENDPOINT` | URL que recibe la lista de espera (contrato abajo). Sin ella el formulario avisa que no está conectado y no envía nada; el build muestra un aviso. |
| `PUBLIC_CONTACT_EMAIL` | Si existe, aparece el enlace "Contacto" en el footer. |
| `PUBLIC_TERMS_URL` | Si existe, aparece "Términos y condiciones" en el footer. Sin ella no se muestra (nada de enlaces muertos). |
| `PUBLIC_PRIVACY_URL` | Si existe, aparece "Política de privacidad" en el footer (y en la nota del formulario). Sin ella no se muestra. |

### Contrato del endpoint de la lista de espera

- **Con JS** (lo normal): `POST` con `Content-Type: application/json` y cuerpo `{"tipo": "comercio" | "prestamista", "nombre": "…", "correo": "…", "ciudad": "…"}`. Cualquier respuesta 2xx es éxito; otra cosa se muestra como error y los datos quedan en el formulario (timeout de 15 s).
- **CORS**: si el endpoint está en otro origen, `application/json` dispara un *preflight*. Debe responder `OPTIONS` con `Access-Control-Allow-Origin: <dominio del sitio>`, `Access-Control-Allow-Methods: POST, OPTIONS` y `Access-Control-Allow-Headers: Content-Type, Accept`, y repetir `Access-Control-Allow-Origin` en la respuesta del `POST`. Sin eso el navegador bloquea el envío.
- **Sin JS**: el formulario nativo envía `application/x-www-form-urlencoded` (`tipo, nombre, correo, ciudad, website`) y el navegador navega a la respuesta, así que el endpoint también debe aceptar urlencoded, descartar en silencio los envíos con el campo trampa `website` lleno y responder con una página o un `303` de vuelta a `/#lista-de-espera`.

## Accesibilidad y movimiento

- **Sin botón de pausa** (decisión de producto). Lo que se repite se detiene solo: el diagrama de "Cómo funciona" hace 3 vueltas y descansa en su estado final (vuelve a empezar al regresar a la sección); la aurora del hero es luz ambiental lenta, a 30 fps, y se apaga fuera de pantalla o con la pestaña oculta. Con `prefers-reduced-motion: reduce` no hay ninguna animación.

- **Enlaces internos**: las secciones bajo el pliegue usan `content-visibility: auto`, así que no hay `scroll-behavior: smooth` en CSS (el navegador calcularía el destino con alturas provisionales y caería lejos). `Layout.astro` intercepta los enlaces `#…`: renderiza todas las secciones, hace el scroll suave (instantáneo con movimiento reducido), corrige al terminar y mueve el foco al destino. También corrige la llegada con `#hash` en la URL. Tras la carga, en `requestIdleCallback`, cada sección se renderiza una vez para que `contain-intrinsic-size: auto` recuerde su altura real.

## Producción: ojo con el minificador de CSS

`vite.build.cssTarget` en `astro.config.mjs` debe listar navegadores explícitos (incluidos Safari y Firefox). Sin eso, el minificador Lightning CSS fusiona `animation-timeline` dentro del shorthand `animation`, Chrome lo descarta y **todas las animaciones por scroll mueren solo en producción** (en `dev` funcionan). Reglas:

- Escribir siempre primero `animation: …` y después `animation-timeline: …` (y `animation-range`).
- Verificar cada build: `npm run build && grep -c "animation-timeline" dist/index.html` debe ser mayor que 0, y revisar el sitio servido desde `dist/` (`npm run preview`), no solo en `dev`.

## Estructura

```
docs/DESIGN.md            sistema visual (dirección Órbita) y patrones prohibidos: leer antes de diseñar
docs/COPY.md              copy final (fuente única de textos, con fuentes de las cifras)
docs/SCREENSHOTS.md       capturas de la app pendientes y cómo reemplazar cada placeholder
src/styles/tokens.css     paleta, tipografía, espaciado y motion (reglas de contraste documentadas)
src/styles/global.css     base, utilidades y sistema de reveal por scroll
src/layouts/Layout.astro  <head>, SEO/OG, fuentes precargadas, enlaces internos y reveal de respaldo
src/components/ui/        Section, SectionHeading, Button, Logo, DeviceFrame
src/components/sections/  una sección por archivo (orden en src/sections.ts)
src/components/screens/   mocks HTML/CSS de las pantallas de la app (placeholders)
src/scripts/motion.ts     utilidades de animación (visibilidad, loops que se pausan fuera de pantalla, count-up)
src/scripts/format.ts     formato de montos y porcentajes (`$48.000`, `15 %`)
scripts/                  contraste, parche de la fuente, fuente de la imagen OG
```

## Marca

- **Colores**: `#191919` (base), `#6930F9` (morado eléctrico), `#9D67FB`, `#B499FE`, `#F1E9FD`. Ver `branding/trust.md`.
- **Contraste**: el morado eléctrico no lleva texto sobre negro (2,8:1); se usa en rellenos, glow y bordes. El texto de acento sobre oscuro es lavanda `#B499FE` (7,5:1). En secciones claras (`#F1E9FD`) el morado sí funciona como texto (5,2:1). Los botones morados llevan texto blanco (6,2:1). `npm run contrast` verifica todos los pares.
- **Logo**: `src/assets/brand/trust-wordmark-light.png` (para fondos oscuros) y `trust-wordmark-dark.png` (para fondos claros). Astro los optimiza a WebP con `srcset`.
- **Favicon / íconos**: derivados del ícono de app de `branding/TRUST - OPEN PAYMENTS.pdf` (página 6).

## Tipografía

- **Qontra** (Tomaz Hrastar) para titulares. La única versión pública es *Regular* y no trae `á é í ó ú ñ ¿ ¡ ’ ”`. `scripts/fonts/patch-qontra.py` agrega esos glifos con el acento y la construcción de la propia fuente (sin tocar los existentes) y copia el kerning. Resultado: `src/assets/fonts/qontra-trust.woff2` (8 KB).
  - Regenerar: `python3 -m venv .venv && .venv/bin/pip install fonttools brotli && .venv/bin/python scripts/fonts/patch-qontra.py scripts/fonts/qontra-original.woff out.otf src/assets/fonts/qontra-trust.woff2`
  - **Licencia**: los sitios de descarga no coinciden (algunos dicen "free", otros "solo uso personal"). Confirmar con el autor antes del lanzamiento comercial.
  - El manual de marca muestra Qontra de Thin a Bold con itálicas. Si consiguen esa familia completa, basta con agregar las variantes en `astro.config.mjs` (`fonts`) y descartar el parche.
- **Barlow** (OFL, pesos 400 y 600) para texto corrido e interfaz: raíz DIN como Qontra y la geometría cuadrada del logo. Reemplazó a Geist, la fuente por defecto de las herramientas de generación (ver `docs/DESIGN.md`).

Ambas se sirven con la API de fuentes de Astro (precarga y fallback con métricas ajustadas).
