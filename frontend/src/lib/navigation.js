export function loginUrl(expired = false) {
  const query = new URLSearchParams();
  if (expired) query.set('expired', '1');
  query.set('next', location.pathname + location.search + location.hash);
  return '/autenticacion/index.html?' + query;
}

export function nextPage(value, fallback) {
  if (!value?.startsWith('/') || value.startsWith('//') || value.includes('\\')) return fallback;
  const url = new URL(value, location.origin);
  if (url.origin !== location.origin || !/^\/(?:portal-prestamos|crear-prestamo|detalle-prestamo|wallet|administrador|op)\//.test(url.pathname)) return fallback;
  return url.pathname + url.search + url.hash;
}
