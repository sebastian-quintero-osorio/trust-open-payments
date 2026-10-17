export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
export const text = (selector, value) => { const node = $(selector); if (node) node.textContent = value; };
export const show = (selector, visible = true) => { const node = $(selector); if (node) node.hidden = !visible; };
export function el(tag, className = '', value) {
  const node = document.createElement(tag); node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}
export function errorMessage(error, target = '#app-error') {
  let node = $(target);
  if (!node) {
    node = el('p', 'app-error'); node.id = 'app-error'; node.setAttribute('role', 'alert');
    ($('main') || document.body).prepend(node);
  }
  node.hidden = false; node.textContent = error.message || String(error);
}
export async function busy(button, action, errorTarget) {
  if (button.disabled) return;
  button.disabled = true; button.setAttribute('aria-busy', 'true');
  try { return await action(); } catch (error) { errorMessage(error, errorTarget); }
  finally { button.disabled = false; button.removeAttribute('aria-busy'); }
}
export function safeExternal(value) {
  const url = new URL(value);
  if (url.username || url.password || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname)))) throw new Error('La dirección de la wallet no es válida.');
  return url.href;
}
export function polling(callback, delay, { onError = errorMessage, onSuccess = () => {} } = {}) {
  let stopped = false, suspended = false, running = false, timer;
  function schedule() {
    clearTimeout(timer);
    if (!stopped && !suspended) timer = setTimeout(run, delay);
  }
  async function run() {
    if (stopped || suspended || running) return;
    clearTimeout(timer);
    if (!document.hidden) {
      running = true;
      try { await callback(); if (!stopped && !suspended) onSuccess(); }
      catch (error) { if (!stopped && !suspended) onError(error); }
      finally { running = false; }
    }
    schedule();
  }
  const hide = () => { suspended = true; clearTimeout(timer); };
  const restore = event => { if (event.persisted && !stopped) { suspended = false; run(); } };
  const visible = () => { if (!document.hidden) run(); };
  const stop = () => {
    stopped = true; clearTimeout(timer);
    window.removeEventListener('pagehide', hide); window.removeEventListener('pageshow', restore); document.removeEventListener('visibilitychange', visible);
  };
  window.addEventListener('pagehide', hide);
  window.addEventListener('pageshow', restore);
  document.addEventListener('visibilitychange', visible);
  schedule();
  return stop;
}
