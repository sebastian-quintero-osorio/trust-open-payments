Para nuestra aplicación descrita, tenemos que crear las siguientes interfaces descritas en  "Interfaces.txt" . Siguiendo el brand kit en la carpeta "../branding/" tanto el txt como el pdf, vamos a definir las bases del frontend de la aplicación web. El objetivo de este primer acercamiento es prponer diferentes caminos visuales con ejemplos de estilos y elegir uno, para posteriormente desarrollar los mockups.


Ruta elegida. Orbita. Ahora vamos a desarrollar los mockups uno por uno, yo tengo que aprobarlo para pasar al siguiente. Siguiendo "Interfaces.txt". Construyamos la base del portal de prestamos, vista simple inicial y lo vamos enriqueciendo y especializando, todo con el objetivo de que este mockup sea el punto de partida de los demas.


Visualmente me parece bien, esta es la base visual para continuar. Vamos a terminar de desarrollar esta vista. Un detalle adicional a nivel tecnico, en ofertas de prestamo, ademas de la información que actualemnte aparece, debe aparecer la tasa de interes del prestamo. 


Aprobada completamente esta vista, ahora crearemos las vistas complementarias, iniciaremos con solicitar/ofrecer prestamo.


Vamos a dar un redireccionamiendo leve antes de continuar. El apartado de historial, debe estar adentro del portal de prestamos, es decir, es un subvista a la que se puede acceder desde dicho modulo. De esta forma centralizamos los prestamos en el portal y se pueda ver el historial. El historial se podrá filtrar por prestamo firmado, alli se vera su estado (al dia, en retraso, pagado) y ofrecidos/solicitados . Agreguemos esto al portal que tenemos actualmente.


Aprobado, ahora continuaremos con el flujo de ver oferta/ver solicitud. Con sus respectivos botones de solicitar prestamo y ofertar prestamo en dado caso que se vea oferta o solicitud. Puesto que el objetivo es enviar una solicitud sea a la otra parte que debe aprobar y así firmar el prestamo y que se devengue las cuentas y quede en el registro.


Basado en el flujo de los mockups, tenemos que desarrollar una experiencia de usuario optima de modo que se notifique si hay ofertas/solicitudes pendientes de aceptación que nos hayan hecho. Primero, pensemos la idea en texto


si la solicitud está pendiente de aprobacion esa final. el boton cuando estoy en el detalle no deberia decir ver en mi historial, sino ver en mis pendientes


Basandonos en el flujo de los mockups y el Interfaces.txt. Crearemos el mockup del modulo  wallet . donde puedo ver la información de mi wallet, dinero, score si soy prestatario, un qr que puedo imprimir de Bre B colombia listo dado por puntored (crear Qr que simulen breve con puntored marca 
identica a la original) A su vez, veo metricas según sea prestamista o prestatario. Así como un botón grande presenté que diga "ver test wallet" simbolizando con otro color visible que es un link externo


El estilo del boton ver test wallet está bien, pero ponlo arriba a la derecha. donde dice Wallet de demostración. Al prestamista muestrale que tanto ha ganado en su historial, tanto en dinero expresado en la divisa de la wallet y en porcentaje de interes promedio de sus prestamos.


Debemos realizar el mockup de la vista del administrador, primeramente el panel con las metricas de Trust, datos importantes son : Cantidad de prestamos, tasa de interes promedio, dinero recaudado, dinero pagado a prestamistas, a negocios , etc (Analizar cuales son pertinentes, pensar esto profundamente a nivel pm de Trust) .




Siguiendo el mockup de administrador, vamos a crear el siguiente modulo de panel del administrador: - Panel de admin con todos los prestamos de la aplicación, ofrecido, solicitado, realizado. A su vez, hay otra vista con el detalle de cada prestamo.


SIguiendo el estilo de los mockups, crea las vistas de login, registro, olvide contraseña. El registro me permite elegir si soy prestamista o prestatario.


Analizando profundamente el contrato de open api del backend "../trust-api.openapi.yaml" luego construye el frontend exactamente igual, sin ninguna diferencia a los mockups ya creados. Luego realiza la integración completa con la api, comprueba su funcionamiento usando playwright, comprueba que no haya ningún bug, realiza pruebas. El objetivo no se da por cumplido hasta que no haya un repaso sin un error encontrado o bug en su ejecución.


El backend fue recientemente integrado en "../backend/" siguiendo "../trust-api.openapi.yaml" . El frontend actual fue desarrollado pensando en cumplir el contrato de open api. Verifica que el frontend está correctamente desarrollado, una vez realizada la comprobación, integralo con el backend real y luego comprueba el buen funcionamiento de la aplicación usando playwright.