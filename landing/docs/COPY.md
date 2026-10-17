# Trust: copy final de la landing (es-CO)

Fuente única de textos de la landing. Cada línea es `clave`: texto. Las claves se usan tal cual en el código. Lo que va entre `{{ }}` es un placeholder pendiente; lo que va entre `{ }` es una variable en tiempo de ejecución.

## 0. Convenciones

- `conv.idioma`: español de Colombia, tuteo ("tú") en toda la página.
- `conv.terminos`: "comercio" (quien pide crédito), "prestamista" (quien presta), "cupo", "Bre-B", "Puntored", "wallet", "abono".
- `conv.prohibido`: no usar "goteo", "gotereado" ni "gota a gota" para hablar de Trust. "Gota a gota" solo nombra el crédito informal. Para los pagos al prestamista: "venta a venta", "con cada venta", "abono".
- `conv.prohibido_claims`: Trust no se presenta como "crédito formal", "vigilado" ni "regulado"; no hay tasas, rendimientos, conteos de usuarios, testimonios ni logos de aliados.
- `conv.dinero`: `$48.000` (sin espacio después de `$`, punto de miles).
- `conv.porcentaje`: `15 %` (con espacio, igual que branding/trust.md).
- `conv.hora`: `10:42 a. m.` · `conv.fecha`: `16 oct 2026` (larga: "Viernes, 16 oct 2026").
- `conv.ejemplo`: todo porcentaje de división visible en la landing lleva la etiqueta "Ejemplo" o el pie "Pantalla ilustrativa con datos de ejemplo.".
- `conv.datos`: un solo caso de ejemplo en toda la página (ver sección 13): venta de $48.000 → $40.800 para el comercio · $7.200 para el prestamista (85 % / 15 %).

- `conv.estilo` (revisión Órbita, ver docs/DESIGN.md): sin eyebrows sobre los H2, sin antítesis inventadas ("X no se promete. Se ve…"), sin pares de fragmentos ("Dos rangos. Un acuerdo."), sin rayas largas. Frases concretas con verbo.

## 1. meta

- `meta.lang`: es-CO
- `meta.title`: Trust | Crédito que pagas con tus ventas
- `meta.description`: Pide crédito según tu cupo y págalo con un porcentaje de cada venta con Bre-B. Si prestas, recibe tu parte venta a venta, clara y trazable.
- `meta.og_title`: Trust: cada venta paga un poco de tu crédito
- `meta.og_description`: Crédito para comercios que se paga con sus propias ventas, a través de Bre-B y Puntored. Si prestas, recibes tu parte con cada venta.
- `meta.og_image_alt`: Logo de Trust sobre fondo negro con un brillo morado suave.

## 2. nav

- `nav.logo_aria`: Trust, ir al inicio
- `nav.skip_link`: Saltar al contenido
- `nav.link_como_funciona`: Cómo funciona → `#como-funciona`
- `nav.link_comercios`: Comercios → `#comercios`
- `nav.link_prestamistas`: Prestamistas → `#prestamistas`
- `nav.link_confianza`: Confianza → `#confianza`
- `nav.link_preguntas`: Preguntas → `#preguntas`
- `nav.cta`: Únete a la lista → `#lista-de-espera`
- `nav.menu_open`: Abrir menú
- `nav.menu_close`: Cerrar menú

## 3. hero

- `hero.eyebrow`: (retirado: la pastilla sobre el H1 es un patrón genérico; "Colombia" queda en `hero.reassurance`)
- `hero.h1`: Cada venta paga un poco de tu crédito.
- `hero.h1_alt`: Tus ventas del día pagan tu crédito.
- `hero.sub`: Tus clientes te pagan con Bre-B a través de Puntored. Trust divide cada pago automáticamente: una parte para ti, otra abona a tu crédito.
- `hero.cta_primary`: Quiero crédito para mi comercio → `#lista-de-espera` (preselecciona "Comercio")
- `hero.cta_secondary`: Quiero prestar con Trust → `#lista-de-espera` (preselecciona "Prestamista"; se muestra como enlace de texto)
- `hero.reassurance`: Lista de espera abierta en Colombia, sin compromiso.
- `hero.receipt.title`: Venta con Bre-B
- `hero.receipt.time`: 10:42 a. m.
- `hero.receipt.amount`: $48.000
- `hero.receipt.part_comercio`: Para ti · $40.800
- `hero.receipt.part_prestamista`: A tu crédito · $7.200
- `hero.receipt.note`: 85 % / 15 %, ejemplo
- (retirados: `hero.chip_split`, `hero.chip_cupo`, `hero.chip_abono`; las tres chips flotantes se reemplazan por una sola tarjeta que divide la venta)
- `hero.mockup_screen`: `cupo-dashboard`
- `hero.mockup_caption`: Pantalla ilustrativa con datos de ejemplo.
- `hero.mockup_alt`: Pantalla de la app de Trust con el cupo disponible de Tienda La Esquina y la división de sus ventas del día.

## 4. problema

- `problema.eyebrow`: (no se muestra)
- `problema.h2`: Cuando el banco dice no, el gota a gota dice sí.
- `problema.col_comercio.title`: Si tienes un comercio
- `problema.col_comercio.b1`: Vendes todos los días, pero el crédito formal no llega.
- `problema.col_comercio.b2`: El préstamo informal cobra intereses de usura, por fuera de la ley.
- `problema.col_comercio.b3`: Y cuando el cobro llega a tu puerta, está en juego tu seguridad.
- `problema.col_prestamista.title`: Si prestas dinero
- `problema.col_prestamista.b1`: No sabes a quién prestarle con confianza.
- `problema.col_prestamista.b2`: Quieres que tu dinero trabaje, pero de forma lícita.
- `problema.col_prestamista.b3`: Bancos, entidades financieras y personas enfrentan la misma duda.
- `problema.stat_1.value`: 15,3 %
- `problema.stat_1.label`: de las microempresas registradas tiene un crédito vigente. En las grandes empresas, el 82,1 %.
- `problema.stat_1.ref`: [1]
- `problema.stat_2.value`: 22,9 %
- `problema.stat_2.label`: de los micronegocios que pidieron crédito acudió al gota a gota. Casi 1 de cada 4.
- `problema.stat_2.ref`: [2]
- `problema.stat_3.value`: 666,5 %
- `problema.stat_3.label`: es la tasa anual estimada del gota a gota para las empresas: más de 30 veces la de un banco.
- `problema.stat_3.ref`: [3]
- `problema.stats_note`: Cifras de Colombia.
- `problema.footnote_1`: [1] Banca de las Oportunidades y Superintendencia Financiera de Colombia, Reporte de Inclusión Financiera 2024 (publicado en 2025).
- `problema.footnote_2`: [2] DANE, Encuesta de Micronegocios (EMICRON) 2024, anexo cuadro H.4.
- `problema.footnote_3`: [3] ANIF y Colombia Fintech, Encuesta de Endeudamiento, enero de 2025.
- `problema.impl_note` (no se muestra): cada footnote enlaza a su URL de la sección "Fuentes". Si se retira una cifra, se retira también su nota.

## 5. como-funciona

Ancla: `#como-funciona`

- `como.eyebrow`: (no se muestra)
- `como.h2`: Así se reparte cada venta.
- `como.intro`: El pago de tu cliente se divide automáticamente, según el porcentaje que acordaste al tomar el crédito.
- `como.step_1.title`: Tu cliente paga
- `como.step_1.text`: Tu cliente te paga con Bre-B, el sistema de pagos inmediatos de Colombia. Como cualquier venta del día.
- `como.step_2.title`: Llega a Puntored
- `como.step_2.text`: El pago entra a tu wallet de Puntored, donde se recolectan tus ventas.
- `como.step_3.title`: Trust lo divide
- `como.step_3.text`: Trust aplica el porcentaje acordado: una parte para ti, otra para tu prestamista. Sin que hagas nada.
- `como.step_4.title`: Cada quien recibe
- `como.step_4.text`: Tú recibes tu parte para seguir vendiendo. Tu prestamista recibe su abono, venta a venta.
- `como.diagram.node_cliente`: Cliente
- `como.diagram.node_breb`: Bre-B
- `como.diagram.node_wallet`: Wallet Puntored
- `como.diagram.node_split`: División Trust
- `como.diagram.node_comercio`: Comercio
- `como.diagram.node_prestamista`: Prestamista
- `como.diagram.pulse`: Venta $48.000
- `como.diagram.branch_comercio`: 85 % · $40.800
- `como.diagram.branch_prestamista`: 15 % · $7.200
- `como.diagram.badge`: Ejemplo
- `como.diagram.footnote`: Ejemplo ilustrativo. El porcentaje real se define al acordar cada crédito.
- `como.diagram.aria`: Diagrama: el pago de un cliente viaja por Bre-B hasta la wallet de Puntored, donde Trust lo divide entre el comercio (85 %) y el prestamista (15 %). Porcentajes de ejemplo.
- `como.screen`: (sin teléfono: el diagrama es el único protagonista; la pantalla `division-en-vivo` queda lista para otros usos)

## 6. comercios

Ancla: `#comercios`

- `comercios.eyebrow`: (no se muestra; el H2 deja claro a quién habla)
- `comercios.annotations`: cada beneficio (b1–b4) señala en la pantalla `solicitud-credito` la parte que lo cumple: b1 → vista previa del abono · b2 → "Cupo disponible" · b3 → selector de rango · b4 → selector de porcentaje.
- `comercios.h2`: Crédito para el negocio que levantaste.
- `comercios.sub`: Pide crédito según tu cupo o acepta ofertas de prestamistas. Tú eliges el rango y lo pagas con un porcentaje de tus ventas.
- `comercios.b1.title`: Pagas mientras vendes
- `comercios.b1.text`: Un porcentaje de cada venta con Bre-B abona a tu crédito. Automático, sin filas ni consignaciones.
- `comercios.b2.title`: Tu cupo, claro
- `comercios.b2.text`: Trust calcula tu cupo y te lo muestra. Sabes cuánto puedes pedir antes de pedir.
- `comercios.b3.title`: Tú eliges el rango
- `comercios.b3.text`: Define entre cuánto y cuánto necesitas. Trust te muestra las ofertas que encajan con tu cupo.
- `comercios.b4.title`: Según lo que vendes
- `comercios.b4.text`: El abono es un porcentaje de cada venta: si un día vendes menos, abonas menos.
- `comercios.cta`: Únete como comercio → `#lista-de-espera` (preselecciona "Comercio")
- `comercios.screen`: `solicitud-credito`

## 7. prestamistas

Ancla: `#prestamistas`

- `prestamistas.eyebrow`: (no se muestra: el fondo lila y el H2 marcan el cambio de público)
- `prestamistas.h2`: Presta de forma lícita y mira llegar cada abono.
- `prestamistas.sub`: Define tu rango, publica ofertas o acepta solicitudes. Recibe tu parte de cada venta del comercio, automáticamente y sin tener que cobrar.
- `prestamistas.b1.title`: Decides con información
- `prestamistas.b1.text`: Cada solicitud está dentro del cupo que Trust calcula para el comercio. Sabes su límite.
- `prestamistas.b2.title`: Abonos con cada venta
- `prestamistas.b2.text`: Recibes el porcentaje acordado cada vez que el comercio cobra con Bre-B. Sin cobros manuales.
- `prestamistas.b3.title`: Tú pones el rango
- `prestamistas.b3.text`: Decide entre qué montos quieres prestar. Publica tu oferta o acepta solicitudes que encajen.
- `prestamistas.b4.title`: Lícito y trazable
- `prestamistas.b4.text`: Una forma lícita de poner tu dinero a trabajar. Cada abono queda registrado.
- `prestamistas.note`: Trust no promete rentabilidades. Las condiciones de cada crédito se acuerdan antes de empezar.
- `prestamistas.cta`: Únete como prestamista → `#lista-de-espera` (preselecciona "Prestamista")
- `prestamistas.screen`: `portafolio-prestamista`

## 8. match

Ancla: `#match`

- `match.eyebrow`: (no se muestra)
- `match.h2`: Cuando los rangos coinciden, hay match.
- `match.sub`: Los comercios piden crédito según su cupo y los prestamistas publican ofertas. Cada uno define su rango; Trust cruza ambos y propone el match. Funciona en las dos direcciones.
- `match.step_1`: Pides o publicas tu rango.
- `match.step_2`: Trust cruza rangos y cupo.
- `match.step_3`: Ambos aceptan y empiezan los abonos.
- `match.card_request.label`: Solicitud
- `match.card_request.body`: Tienda La Esquina · Rango $1.500.000 – $3.000.000 · Cupo $6.000.000
- `match.card_offer.label`: Oferta
- `match.card_offer.body`: Camilo P. · Rango $1.000.000 – $2.500.000 · 15 % de cada venta
- `match.card_result.label`: Match
- `match.card_result.body`: $2.000.000 · 15 % de cada venta · Listo para aceptar
- `match.overlap`: Rangos que se cruzan: $1.500.000 – $2.500.000
- `match.badge`: Ejemplo
- `match.caption`: Datos de ejemplo.
- `match.screen`: (sin teléfono: el cruce de rangos es el único protagonista; la pantalla `detalle-match` queda lista para otros usos)

## 9. confianza

Ancla: `#confianza`

- `confianza.eyebrow`: (no se muestra)
- `confianza.h2`: Cada pago queda registrado, de la venta al abono.
- `confianza.p1.title`: Cupo calculado en Trust
- `confianza.p1.text`: El comercio pide dentro de su cupo. El prestamista sabe que cada solicitud tiene un límite claro.
- `confianza.p2.title`: División en el origen
- `confianza.p2.text`: Cada pago se divide automáticamente en la wallet de Puntored, con el porcentaje acordado. Sin pasos manuales.
- `confianza.p3.title`: Pagos trazables
- `confianza.p3.text`: Cada venta, cada división y cada abono quedan registrados. Nada depende de la memoria ni de la palabra.
- `confianza.p4.title`: Cada quien pone su rango
- `confianza.p4.text`: Comercio y prestamista definen su rango. El crédito empieza solo cuando ambos aceptan las condiciones.
- `confianza.registro.title`: Registro de una venta
- `confianza.registro.badge`: Ejemplo
- `confianza.registro.rows` (hora · evento · detalle · monto):
  - 10:42 a. m. · Venta con Bre-B · Tienda La Esquina · $48.000
  - 10:42 a. m. · Llega a la wallet Puntored · Tienda La Esquina · $48.000
  - 10:42 a. m. · División acordada · 85 % comercio / 15 % prestamista · (sin monto)
  - 10:42 a. m. · Para el comercio · Tienda La Esquina · $40.800
  - 10:42 a. m. · Abono al prestamista · Camilo P. · $7.200
- `confianza.registro.foot`: Cada fila queda guardada y se puede rastrear hasta la venta que la originó.
- `confianza.registro.aria`: Ejemplo de registro: una venta de $48.000 con Bre-B llega a la wallet de Puntored, se divide 85 % y 15 %, y se registran $40.800 para el comercio y $7.200 de abono para el prestamista.

## 10. faq

Ancla: `#preguntas`

- `faq.eyebrow`: (no se muestra)
- `faq.h2`: Preguntas frecuentes
- `faq.q1`: ¿Qué es Trust?
- `faq.a1`: Una plataforma que conecta comercios que necesitan crédito con prestamistas que quieren prestar de forma lícita. El crédito se paga con un porcentaje de las ventas que el comercio cobra con Bre-B a través de Puntored.
- `faq.q2`: ¿Cómo pago mi crédito?
- `faq.a2`: Con tus ventas. Cuando un cliente te paga con Bre-B, el dinero llega a tu wallet de Puntored y se divide automáticamente: una parte para ti y otra para tu prestamista, según el porcentaje acordado. No tienes que transferir nada aparte.
- `faq.q3`: Si presto, ¿cómo recibo mi dinero?
- `faq.a3`: Tu parte se separa automáticamente de cada pago que el comercio recibe con Bre-B en su wallet de Puntored. Te llega venta a venta, según el porcentaje acordado. Tú decides entre qué montos quieres prestar.
- `faq.q4`: ¿Qué es el cupo y cómo se calcula?
- `faq.a4`: Es el monto máximo que puedes pedir. Trust lo calcula para cada comercio y lo ves en la app antes de solicitar. Los criterios de cálculo se publicarán en el lanzamiento; únete a la lista para conocerlos primero.
- `faq.q5`: ¿Qué pasa si un día vendo menos?
- `faq.a5`: Ese día abonas menos, porque el abono es un porcentaje de cada venta. Los demás términos de cada crédito, como el plazo, se darán a conocer en el lanzamiento.
- `faq.q6`: ¿Cuánto cuesta un crédito? ¿Cuál es la tasa?
- `faq.a6`: Todavía no está definido. Las tasas y los costos se publicarán en el lanzamiento, y verás las condiciones de cada crédito antes de aceptarlo. Trust no promete rentabilidades a los prestamistas.
- `faq.q7`: ¿Qué necesito para empezar y dónde estará disponible?
- `faq.a7`: Empezamos en Colombia. Como comercio, cobrarás tus ventas con Bre-B a través de Puntored. Como prestamista, puedes ser una entidad financiera, un banco o una persona. Los requisitos completos y los próximos países se anunciarán más adelante.

## 11. cta-final

Ancla: `#lista-de-espera`

- `cta.h2`: Crédito con la frente en alto.
- `cta.sub`: Únete a la lista de espera como comercio o como prestamista. Te avisaremos cuando abramos el acceso.
- `form.required_hint`: Todos los campos son obligatorios.
- `form.tipo.label`: Soy
- `form.tipo.option_comercio`: Comercio · quiero crédito
- `form.tipo.option_prestamista`: Prestamista · quiero prestar
- `form.tipo.error`: Elige si eres comercio o prestamista.
- `form.nombre.label`: Nombre
- `form.nombre.placeholder`: Nombre y apellido
- `form.nombre.error`: Escribe tu nombre.
- `form.correo.label`: Correo electrónico
- `form.correo.placeholder`: tucorreo@ejemplo.com
- `form.correo.error_vacio`: Escribe tu correo.
- `form.correo.error_formato`: Revisa tu correo. Parece que falta algo.
- `form.ciudad.label`: Ciudad
- `form.ciudad.placeholder`: Ej.: Bucaramanga
- `form.ciudad.error`: Escribe tu ciudad.
- `form.submit`: Unirme a la lista
- `form.privacy`: Solo usaremos tus datos para avisarte sobre Trust. Consulta la Política de privacidad. (enlace a `#privacidad`)
- `form.state.sending`: Enviando…
- `form.state.success_title`: Listo, {nombre}. Ya estás en la lista.
- `form.state.success_title_fallback`: Listo, ya estás en la lista.
- `form.state.success_text`: Te avisaremos cuando abramos el acceso.
- `form.state.error`: No pudimos enviar tus datos. Revisa tu conexión e inténtalo de nuevo.
- `form.state.dev_not_connected` (solo en desarrollo): Formulario aún no conectado (modo desarrollo). Tus datos no se enviaron ni se guardaron.

## 12. footer

- `footer.tagline`: Crédito que se paga con tus ventas.
- `footer.group_producto.title`: Producto
- `footer.group_producto.links`: Cómo funciona (`#como-funciona`) · El match (`#match`) · Confianza (`#confianza`)
- `footer.group_para_ti.title`: Para ti
- `footer.group_para_ti.links`: Comercios (`#comercios`) · Prestamistas (`#prestamistas`) · Lista de espera (`#lista-de-espera`)
- `footer.group_ayuda.title`: Ayuda
- `footer.group_ayuda.links`: Preguntas frecuentes (`#preguntas`) · Contacto (`mailto:{{CORREO_CONTACTO}}`)
- `footer.legal.terminos`: Términos y condiciones (`#terminos`, placeholder)
- `footer.legal.privacidad`: Política de privacidad (`#privacidad`, placeholder)
- `footer.brands_note` (pendiente de revisión legal): Puntored y Bre-B son marcas de sus respectivos titulares.
- `footer.copyright`: © 2026 Trust

## 13. app-screens

Las capturas reales llegan después. Mientras tanto, cada pantalla se construye como placeholder fiel a esta especificación. Las cinco cuentan una sola historia: solicitud → match → división en vivo → portafolio → cupo.

### 13.0 Datos y estilo compartidos

- `screens.data.comercio`: Tienda La Esquina · Marta R. · Bucaramanga
- `screens.data.prestamista`: Camilo P. (el mismo en el match, el feed y el portafolio)
- `screens.data.cupo_total`: $6.000.000
- `screens.data.cupo_antes`: antes del crédito se muestra como "Cupo $6.000.000", sin "disponible", para no chocar con los $4.000.000 del hero (`match`, `solicitud-credito`, `detalle-match`)
- `screens.data.cupo_despues`: $4.000.000 disponibles (con el crédito activo de $2.000.000; hero y `cupo-dashboard`)
- `screens.data.split`: 85 % comercio / 15 % prestamista (ejemplo)
- `screens.data.venta`: $48.000 → $40.800 para el comercio · $7.200 para el prestamista
- `screens.data.hoy`: 14 ventas · $612.000 → $520.200 para el comercio · $91.800 abonado
- `screens.data.fechas`: oferta recibida 1 oct 2026 · crédito activo desde 2 oct 2026 · hoy viernes, 16 oct 2026
- `screens.leyendas`: sin viñetas de color; el color del monto indica de quién es (lila: comercio · lavanda: abono al prestamista).
- `screens.style`: marco de teléfono, interfaz oscura (fondo #191919, superficies #1F1D25), texto principal #F1E9FD, texto secundario #BDB3CF. El morado #6930F9 solo para rellenos, bordes y glow, nunca como color de texto sobre oscuro; el texto de acento va en lavanda #B499FE. Botones dentro de la app: relleno #6930F9 con texto blanco (en la landing, el CTA primario es lila #F1E9FD con texto #191919, según tokens.css).
- `screens.caption`: Pantalla ilustrativa con datos de ejemplo. (pie común bajo cada captura)
- `screens.asset`: `public/screens/<id>.avif` con respaldo `.webp`, 780 × 1688 px (390 × 844 @2x), carga diferida salvo la del hero.
- `screens.no_mostrar`: tasas, costos, rendimientos, nombres de bancos o logos de terceros.

### 13a. `cupo-dashboard`

- `screens.cupo-dashboard.where`: hero (teléfono principal, con los 3 chips flotando encima).
- `screens.cupo-dashboard.title`: Tu cupo
- `screens.cupo-dashboard.elements`:
  - Barra de estado: 9:41
  - Encabezado: "Hola, Marta" · avatar con iniciales "TL" · "Tienda La Esquina · Bucaramanga" · ícono de notificaciones (sin punto: nada de testigos, ver docs/DESIGN.md)
  - Tarjeta principal (vidrio, glow morado): "Cupo disponible" · **$4.000.000** · línea secundaria "Cupo total $6.000.000"
  - Tarjeta "Crédito activo": $2.000.000 · "15 % de cada venta" · "Prestamista: Camilo P." · barra de avance "Pagado $640.000 · 32 %" · "Desde 2 oct 2026"
  - Tarjeta "Ventas de hoy con Bre-B": $612.000 · "14 ventas" · "Para ti $520.200" · "Abonado $91.800"
  - Accesos: "Ver ventas" · "Ofertas para ti (3)" · "Historial"
  - Barra inferior: Inicio (activo) · Ventas · Créditos · Perfil
- `screens.cupo-dashboard.primary_action`: botón "Pedir crédito"
- `screens.cupo-dashboard.alt`: App de Trust: Tienda La Esquina tiene $4.000.000 de cupo disponible y un crédito activo de $2.000.000 pagado en un 32 %.

### 13b. `solicitud-credito`

- `screens.solicitud-credito.where`: sección Comercios, junto a "Tú eliges el rango" y "Tu cupo, claro".
- `screens.solicitud-credito.title`: Solicitar crédito
- `screens.solicitud-credito.elements`:
  - Flecha atrás · "Paso 2 de 3 · Monto"
  - Recordatorio: "Tu cupo: $6.000.000"
  - Selector de rango de dos puntos: mínimo **$1.500.000** · máximo **$3.000.000** · escala de $500.000 a $6.000.000 · tramo elegido con relleno morado
  - Selector "Porcentaje de tus ventas para abonar": 10 % · **15 %** (seleccionado) · 20 %
  - Vista previa en vivo: "Con 15 %, una venta de $48.000 abona $7.200 y te quedan $40.800."
  - Aviso: "Las condiciones finales se acuerdan con el prestamista antes de aceptar."
  - Contador: "3 ofertas encajan con tu rango"
- `screens.solicitud-credito.primary_action`: botón "Publicar solicitud" · secundaria "Ver ofertas que encajan"
- `screens.solicitud-credito.alt`: App de Trust: solicitud de crédito con un rango de $1.500.000 a $3.000.000 y un abono del 15 % de cada venta.

### 13c. `division-en-vivo`

- `screens.division-en-vivo.where`: sección Cómo funciona, al lado del diagrama animado.
- `screens.division-en-vivo.title`: Ventas de hoy
- `screens.division-en-vivo.elements`:
  - Fecha: "Viernes, 16 oct 2026" (sin indicador "En vivo": los testigos están prohibidos, ver docs/DESIGN.md)
  - Resumen: "Ventas $612.000" · "Para ti $520.200" · "Abonado a tu crédito $91.800" · "14 ventas"
  - Regla fija sobre la lista: "85 % para ti · 15 % a tu crédito"
  - Movimientos (cada fila con barra dividida 85/15; la primera entra con animación):
    - 10:42 a. m. · Venta Bre-B **$48.000** → Para ti $40.800 · Crédito $7.200
    - 10:31 a. m. · Venta Bre-B $26.000 → Para ti $22.100 · Crédito $3.900
    - 10:05 a. m. · Venta Bre-B $12.500 → Para ti $10.625 · Crédito $1.875
    - 9:47 a. m. · Venta Bre-B $85.000 → Para ti $72.250 · Crédito $12.750
  - Enlace: "Ver las 14 ventas"
  - Pie: "En tu wallet Puntored hoy: $520.200"
- `screens.division-en-vivo.primary_action`: botón "Ver detalle del crédito"
- `screens.division-en-vivo.alt`: App de Trust: ventas del día divididas en vivo, 85 % para el comercio y 15 % para su crédito.

### 13d. `portafolio-prestamista`

- `screens.portafolio-prestamista.where`: sección Prestamistas.
- `screens.portafolio-prestamista.title`: Mi portafolio
- `screens.portafolio-prestamista.elements`:
  - Encabezado: "Hola, Camilo" · "3 créditos activos"
  - Tarjeta principal: "Total prestado" **$5.500.000** · "Abonos de hoy" **+$186.400** (contador animado)
  - Chip: "Mi rango: $1.000.000 – $2.500.000" con ícono de editar
  - Gráfico "Abonos de hoy por hora": 8:00 a. m. $38.100 · 9:00 a. m. $71.500 · 10:00 a. m. $76.800
  - Lista de créditos:
    - Tienda La Esquina · $2.000.000 · 15 % de cada venta · Pagado 32 % · hoy +$91.800
    - Panadería El Trigal · $1.500.000 · 12 % de cada venta · Pagado 58 % · hoy +$58.200
    - Papelería Nova · $2.000.000 · 10 % de cada venta · Pagado 12 % · hoy +$36.400
  - "Últimos abonos":
    - 10:42 a. m. · Tienda La Esquina · +$7.200
    - 10:31 a. m. · Tienda La Esquina · +$3.900
    - 10:15 a. m. · Panadería El Trigal · +$4.500
  - Nota discreta: "Cada abono es trazable hasta la venta que lo originó."
- `screens.portafolio-prestamista.primary_action`: botón "Publicar oferta" · secundaria "Ver solicitudes"
- `screens.portafolio-prestamista.alt`: App de Trust: portafolio de un prestamista con tres créditos activos y los abonos que llegan con cada venta.

### 13e. `detalle-match`

- `screens.detalle-match.where`: sección Match, como pantalla final del visual solicitud → oferta → match.
- `screens.detalle-match.title`: Match encontrado
- `screens.detalle-match.elements`:
  - Sello superior: "Match" (estático)
  - Tarjeta "Tu solicitud": Tienda La Esquina · Rango $1.500.000 – $3.000.000 · Cupo $6.000.000
  - Tarjeta "Oferta": Camilo P. · Rango $1.000.000 – $2.500.000 · "Oferta recibida: 1 oct 2026"
  - Franja de rangos superpuestos: "Rangos que se cruzan: $1.500.000 – $2.500.000"
  - Propuesta: "Monto $2.000.000" · "15 % de cada venta con Bre-B"
  - Vista previa: "Una venta de $48.000 → $40.800 para ti · $7.200 para Camilo P."
  - Enlace: "Ver condiciones completas" (sin cifras de tasa ni costo: aún no están definidas)
  - Nota: "Al aceptar, el porcentaje acordado se aplicará a tus ventas cobradas con Bre-B a través de Puntored."
- `screens.detalle-match.primary_action`: botón "Aceptar oferta" · secundaria "Ver otras ofertas"
- `screens.detalle-match.alt`: App de Trust: match entre la solicitud de Tienda La Esquina y la oferta de un prestamista por $2.000.000, con el 15 % de cada venta.

## Fuentes

Solo las cifras usadas en la sección "El problema".

1. **15,3 % vs. 82,1 %**: Banca de las Oportunidades y Superintendencia Financiera de Colombia, *Reporte de Inclusión Financiera (RIF) 2024*, cap. 6. Año: 2024 (publicado en mayo de 2025). https://www.bancadelasoportunidades.gov.co/sites/default/files/2025-05/BDO_RIF%202024_COMPLETO%201.pdf · Mide personas jurídicas con registro activo en el RUES; no incluye micronegocios informales, por eso el texto dice "microempresas registradas". Confianza: alta.
2. **22,9 %**: DANE, *Encuesta de Micronegocios (EMICRON) 2024*, anexo cuadro H.4 (y boletín técnico, sec. 1.11). Año: 2024, publicada el 30 jul 2025 (referencia del crédito: 2023). Anexo: https://www.dane.gov.co/files/operaciones/EMICRON/anex-EMICRON-2024.xlsx · Boletín: https://www.dane.gov.co/files/operaciones/EMICRON/bol-EMICRON-2024.pdf · Base: micronegocios que solicitaron crédito. No usar el 77,0 % "rural gota a gota" del boletín (error de tabla). Confianza: alta.
3. **666,5 % anual**: ANIF y Colombia Fintech, *Encuesta de Endeudamiento*, Informe Semanal ANIF del 27 ene 2025 (encuesta 2024). https://anif.com.co/wp-content/uploads/encuesta-de-endeudamiento-una-nueva-perspectiva-de-la-exclusion-financiera-liderada-por-anif-y-colombia-fintech-2.pdf · Estimación de encuesta (1.009 MiPymes). Texto de la fuente: la tasa del gota a gota para empresas "supera en más de 30 veces la ofrecida por los bancos" (bancos: 13,0 %, gráfico 6). No calcular múltiplos frente a la tasa de usura. Confianza: media.

## Decisiones

1. Gana el borrador A (Dignidad y libertad), 8,5 frente a 7,8: más claro para un tendero y con un solo caso de datos ($48.000 → $40.800 / $7.200) que cuadra en chips, diagrama y pantallas; B mezclaba ventas de $120.000 y $100.000 y un match que contradecía su portafolio.
2. Del borrador B: el H1 "Cada venta paga un poco de tu crédito.", el H2 del match, las anclas y los CTA que preseleccionan el tipo de usuario, la pregunta para prestamistas y el éxito con {nombre}.
3. Se eliminó por fact-check: "crédito formal" aplicado a Trust, "sin cobradores en la puerta", "sin costo", "nadie presta a ciegas" y todo "goteo", para no confundirlo con el gota a gota.
4. Cifras: 3 de 9 (RIF 2024, EMICRON 2024, ANIF 2025). Se descartó el 85,8 % porque no pedir crédito no es lo mismo que no tener acceso.
5. El prestamista de ejemplo pasa a "Camilo P." para evitar cualquier lectura de alianza, y ninguna pantalla muestra tasas ni costos.
