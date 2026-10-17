import { operations } from './operations.js';
import { loginUrl } from './navigation.js';

const SESSION_KEY = 'trust.session.v1';
let token = sessionStorage.getItem(SESSION_KEY);
export class ApiError extends Error {
  constructor(code, message, status = 0, details = null) {
    super(message); this.name = 'ApiError'; this.code = code; this.status = status; this.details = details;
  }
}
export const session = {
  get authenticated() { return Boolean(token); },
  save(value) { sessionStorage.setItem(SESSION_KEY, value); token = value; },
  clear() { token = null; sessionStorage.removeItem(SESSION_KEY); },
};

export async function api(operation, { params = {}, query = {}, body, signal } = {}) {
  const definition = operations[operation];
  if (!definition) throw new Error(`Unknown operation: ${operation}`);
  const path = definition.path.replace(/\{([^}]+)\}/g, (_, key) => {
    if (!params[key]) throw new Error(`Missing parameter: ${key}`);
    return encodeURIComponent(params[key]);
  });
  const config = window.TRUST_CONFIG || {};
  const base = (config.apiBaseUrl || '').replace(/\/$/, '');
  const url = new URL(base + path, location.origin);
  for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value));
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(abort, config.requestTimeoutMs || 15000);
  try {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (token && !definition.public) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(url, { method: definition.method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal, credentials: 'omit', cache: 'no-store' });
    if (response.status === 204) return null;
    let data;
    try { data = await response.json(); }
    catch { throw new ApiError('invalid_response', 'El servidor devolvió una respuesta no válida. Inténtalo de nuevo.', response.status); }
    if (!response.ok) {
      if (response.status === 401 && !definition.public) {
        session.clear();
        location.replace(loginUrl(true));
      }
      throw new ApiError(data.error?.code || 'http_error', data.error?.message || 'No se pudo completar la operación.', response.status, data.error?.details);
    }
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (signal?.aborted) throw error;
    throw new ApiError(controller.signal.aborted ? 'timeout' : 'network_error', controller.signal.aborted ? 'El servidor está tardando demasiado. Inténtalo de nuevo.' : 'No pudimos conectar con Trust. Revisa tu conexión e inténtalo de nuevo.');
  } finally {
    clearTimeout(timeout); signal?.removeEventListener('abort', abort);
  }
}

export async function allPages(operation, options = {}) {
  const { onPage, ...request } = options;
  const items = [], offsets = new Set();
  let offset = 0;
  do {
    if (offsets.has(offset)) throw new ApiError('invalid_pagination', 'No se pudo cargar la lista completa. Inténtalo de nuevo.');
    offsets.add(offset);
    const page = await api(operation, { ...request, query: { ...request.query, limit: 200, offset } });
    onPage?.(page, offset);
    items.push(...page.items);
    offset = page.next_offset;
  } while (offset !== null && offset !== undefined);
  return items;
}
