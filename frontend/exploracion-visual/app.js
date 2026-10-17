const routes = {
  precision: {
    title: 'Cada cifra en su lugar.',
    description: 'Una interfaz de lectura rápida: superficies mates, líneas nítidas y menor separación entre registros. El morado señala acciones y datos relevantes.',
    material: 'Superficies mates + acento puntual', shape: 'Radios de 4–8 px', motion: 'Transiciones de 160 ms',
    feel: 'Precisa, sobria y estable. Los montos se leen primero; los efectos se reducen para dar protagonismo a la información.',
    fit: 'Comparación de ofertas, historial y panel de administración. Favorece a quienes revisan muchos préstamos en una sesión.',
    tradeoff: 'Puede sentirse más institucional y menos cercana. El registro y la explicación del reparto necesitan textos especialmente claros.'
  },
  flujo: {
    title: 'El dinero se mueve. Tú ves el camino.',
    description: 'Superficies suaves, jerarquía abierta y una división visible de cada venta. La tecnología se entiende a través de lo que hace por tu negocio.',
    material: 'Superficies sólidas + luz localizada', shape: 'Radios de 16–24 px', motion: 'Transiciones de 220 ms',
    feel: 'Cercana, digital y comprensible. El reparto es el protagonista; los montos y las acciones conservan espacio para respirar.',
    fit: 'Registro, creación de wallet y solicitud de crédito. También permite una variante compacta para prestamistas y administración.',
    tradeoff: 'El espacio generoso reduce la cantidad de registros visibles. Las tablas necesitan una densidad propia, sin trasladarles todos los paneles.'
  },
  orbita: {
    title: 'Tu negocio, en primer plano.',
    description: 'Un foco de luz enmarca el cupo del comercio. Profundidad, reflejos suaves y una superficie de vidrio puntual interpretan el lado más expresivo del kit.',
    material: 'Luz ambiental + un panel de vidrio', shape: 'Radios de 20–28 px', motion: 'Transiciones de 320 ms',
    feel: 'Inmersiva y sofisticada. El cupo se percibe como un espacio propio dentro del ecosistema de Trust.',
    fit: 'Bienvenida, creación de wallet y resumen de cuenta. Construye reconocimiento visual en los momentos principales de la experiencia.',
    tradeoff: 'El brillo y las transparencias requieren más cuidado con contraste y rendimiento. Formularios y tablas deben mantener superficies sólidas.'
  }
};
const tabs = [...document.querySelectorAll('[data-route]')];
const panel = document.querySelector('#route-panel');
const bindings = { title: 'route-title', description: 'route-description', material: 'route-material', shape: 'route-shape', motion: 'route-motion', feel: 'route-feel', fit: 'route-fit', tradeoff: 'route-tradeoff' };
function activateRoute(route, updateUrl = true) {
  if (!Object.hasOwn(routes, route)) return;
  panel.dataset.theme = route;
  panel.setAttribute('aria-labelledby', `tab-${route}`);
  tabs.forEach(tab => { const active = tab.dataset.route === route; tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1; });
  Object.entries(bindings).forEach(([key, id]) => { document.getElementById(id).textContent = routes[route][key]; });
  if (updateUrl) {
    const url = new URL(location.href);
    url.searchParams.set('ruta', route);
    try { history.replaceState(null, '', url); } catch { /* File previews can restrict history. */ }
  }
}
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateRoute(tab.dataset.route));
  tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length - 1;
    if (next === undefined) return;
    event.preventDefault(); tabs[next].focus(); activateRoute(tabs[next].dataset.route);
  });
});
const requestedRoute = new URLSearchParams(location.search).get('ruta');
if (requestedRoute && Object.hasOwn(routes, requestedRoute)) activateRoute(requestedRoute, false);
const preference = document.querySelector('#preferred-route');
const selectionNote = document.querySelector('#selection-note');
try { const stored = localStorage.getItem('trust.visual-direction'); if (stored && Object.hasOwn(routes, stored)) preference.value = stored; } catch { /* Selection remains available without storage. */ }
preference.addEventListener('change', () => {
  try {
    if (preference.value) localStorage.setItem('trust.visual-direction', preference.value); else localStorage.removeItem('trust.visual-direction');
    selectionNote.textContent = preference.value ? `Preferencia guardada: ${preference.selectedOptions[0].textContent}. Compártela para continuar con los mockups.` : 'Sin dirección elegida. Puedes seguir comparando las propuestas.';
  } catch { selectionNote.textContent = 'Selección disponible durante esta visita. El navegador no permitió guardarla; compártela para continuar.'; }
});
const amount = document.querySelector('#sample-amount');
const amountError = document.querySelector('#amount-error');
amount.addEventListener('input', () => {
  const raw = amount.value.trim();
  const validFormat = /^(?:\d+|\d{1,3}(?:\.\d{3})+)$/.test(raw);
  const value = validFormat ? Number(raw.replaceAll('.', '')) : NaN;
  const message = !Number.isFinite(value) || value <= 0 ? 'Escribe un monto mayor que cero, sin decimales.' : value > 8000000 ? 'El monto supera el cupo de $8.000.000. Ingresa un monto dentro de tu cupo.' : '';
  amountError.textContent = message;
  amount.setAttribute('aria-invalid', String(Boolean(message)));
  amount.setAttribute('aria-describedby', message ? 'amount-help amount-error' : 'amount-help');
});
