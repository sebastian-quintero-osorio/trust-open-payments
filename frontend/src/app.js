import { api, session, allPages } from './lib/api.js';
import { $, $$, el, text, errorMessage, polling, busy } from './lib/ui.js';
import { loginUrl } from './lib/navigation.js';

export function home(role) {
  return role === 'admin' ? '/administrador/index.html' : `/portal-prestamos/${role === 'lender' ? 'prestamista' : 'index'}.html`;
}
function prepareChrome() {
  // Review annotations belong to the reference artifact, not to the product.
  $$('.review-bar, .review-footer, #scope-dialog, .demo-note, .simulation-label').forEach(node => node.remove());
  document.title = document.title.replace(/ · Mockup.*/, '');
}
function shell(me) {
  text('.account strong', me.business_name || me.public_name);
  text('.account .avatar', me.initials);
  text('.account div > span', ({ merchant: 'Prestatario', lender: 'Prestamista', admin: 'Administrador' })[me.role]);
  text('.account-note', ({ merchant: 'Cuenta de comercio', lender: 'Cuenta de prestamista', admin: 'Vista consolidada de la plataforma' })[me.role]);
  const walletPath = `/wallet/${me.role === 'lender' ? 'prestamista' : 'index'}.html`;
  $$('[data-wallet]').forEach(a => a.href = walletPath);
  $$('a[data-history], #back-portal').forEach(a => a.href = home(me.role) + (a.hasAttribute('data-history') ? '#historial' : ''));
  if (me.role !== 'admin') $$('.sidebar-brand a, .main-nav a, .back-link').forEach(a => {
    if (a.getAttribute('href')?.includes('portal-prestamos')) a.href = home(me.role);
  });
  const logout = el('button', 'session-logout', 'Cerrar sesión'); logout.type = 'button';
  logout.onclick = () => { session.clear(); location.replace('/autenticacion/index.html'); };
  $('.sidebar-bottom')?.append(logout);
  const sidebar = $('#sidebar'), overlay = $('#menu-overlay'), toggle = $('#open-menu'), workspace = $('.workspace');
  if (!sidebar || !toggle) return;
  const media = matchMedia('(max-width: 700px)');
  function close(focus = true) {
    sidebar.classList.remove('is-open'); overlay.hidden = true;
    document.body.classList.remove('menu-open'); toggle.setAttribute('aria-expanded', 'false'); workspace.inert = false;
    if (focus && media.matches) toggle.focus();
  }
  toggle.onclick = () => {
    sidebar.classList.add('is-open'); overlay.hidden = false; document.body.classList.add('menu-open');
    toggle.setAttribute('aria-expanded', 'true'); workspace.inert = true; $('#close-menu').focus();
  };
  $('#close-menu').onclick = () => close(); overlay.onclick = () => close();
  media.addEventListener('change', () => close(false));
  sidebar.querySelectorAll('a').forEach(a => a.addEventListener('click', () => close(false)));
  document.addEventListener('keydown', event => {
    if (!sidebar.classList.contains('is-open')) return;
    if (event.key === 'Escape') close();
    if (event.key !== 'Tab') return;
    const items = $$('a, button', sidebar).filter(node => node.getClientRects().length);
    if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus(); }
    else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
  });
}
async function notifications(me) {
  if (!$('.topbar')) return;
  const link = el('a', 'pending-access'); link.href = home(me.role) + '#recibidas';
  link.innerHTML = '<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg><span>Pendientes</span>';
  const count = el('span', 'pending-count', '—'); link.append(count);
  if (me.role !== 'admin') $('.topbar').append(link);
  const button = el('button', 'icon-button', 'Avisos'); button.type = 'button'; button.setAttribute('aria-label', 'Notificaciones');
  $('.topbar').append(button);
  const dialog = el('dialog'), title = el('h2', '', 'Notificaciones'), close = el('button', 'button button-detail', 'Cerrar');
  const list = el('div', 'app-notifications'), readAll = el('button', 'button button-detail', 'Marcar todas como leídas');
  const error = el('p', 'app-error'); error.id = 'notifications-error'; error.setAttribute('role', 'alert'); error.hidden = true;
  title.id = 'notifications-title'; dialog.setAttribute('aria-labelledby', title.id);
  close.onclick = () => dialog.close(); dialog.append(title, error, list, readAll, close); document.body.append(dialog);
  let refreshVersion = 0;
  async function refresh() {
    const version = ++refreshVersion;
    let unreadCount;
    const [items, loans] = await Promise.all([allPages('listNotifications', { onPage: (page, offset) => { if (offset === 0) unreadCount = page.unread_count; } }), me.role === 'admin' ? [] : allPages('listLoans')]);
    if (version !== refreshVersion) return;
    const pending = loans.filter(loan => loan.next_action.actor === me.role && loan.next_action.type !== 'none').length;
    count.textContent = pending; link.classList.toggle('has-pending', pending > 0); link.setAttribute('aria-label', `Pendientes: ${pending} acuerdos por responder`);
    const unread = unreadCount ?? items.filter(n => !n.read_at).length;
    button.setAttribute('aria-label', `Notificaciones: ${unread} sin leer`);
    list.replaceChildren();
    if (!items.length) list.append(el('p', '', 'No tienes notificaciones.'));
    for (const item of items) {
      const article = el('article'); article.append(el('h3', '', item.title), el('p', '', item.body));
      if (item.data.loan_id) {
        const a = el('a', '', 'Ver préstamo'); a.href = (me.role === 'admin' ? '/administrador/prestamo.html?id=' : '/detalle-prestamo/oferta.html?loan_id=') + encodeURIComponent(item.data.loan_id); article.append(a);
      }
      if (!item.read_at) {
        const read = el('button', 'button button-detail', 'Marcar como leída');
        read.onclick = () => busy(read, async () => { error.hidden = true; await api('readNotification', { params: { notification_id: item.id } }); await refresh(); }, '#notifications-error');
        article.append(read);
      }
      list.append(article);
    }
  }
  readAll.onclick = () => busy(readAll, async () => { error.hidden = true; await api('readAllNotifications'); await refresh(); }, '#notifications-error');
  button.onclick = () => dialog.showModal();
  document.addEventListener('trust:loans-changed', () => refresh().catch(failure => errorMessage(failure, '#notifications-error')));
  try { await refresh(); } catch (failure) { errorMessage(failure, '#notifications-error'); }
  polling(refresh, 30000, { onError: failure => errorMessage(failure, '#notifications-error'), onSuccess: () => { error.hidden = true; } });
}

async function start() {
  prepareChrome();
  if (location.pathname.startsWith('/autenticacion/')) {
    const { init } = await import('./pages/auth.js'); await init(); return;
  }
  if (!session.authenticated) {
    if (/^\/op\/return(?:\.html)?$/.test(location.pathname) && new URLSearchParams(location.search).get('client') === 'mobile') {
      const { mobileReturn } = await import('./pages/return.js'); mobileReturn(); return;
    }
    location.replace(loginUrl()); return;
  }
  const main = $('main'); if (main) main.hidden = true;
  const loading = el('p', 'app-loading', 'Cargando tu cuenta…'); loading.id = 'app-loading'; loading.setAttribute('role', 'status');
  ($('.workspace') || document.body).prepend(loading);
  if ($('.app-shell')) $('.app-shell').inert = true;
  text('.account strong', 'Cargando cuenta…'); text('.account .avatar', '—');
  let me;
  try { me = await api('getMe'); } catch (error) { if (main) main.hidden = false; throw error; }
  const path = location.pathname;
  const expected = path.startsWith('/administrador/') ? 'admin' : /prestamista.html|ofrecer.html/.test(path) ? 'lender' : /portal-prestamos\/index|wallet\/index|solicitar.html/.test(path) ? 'merchant' : null;
  if ((expected && expected !== me.role) || (me.role === 'admin' && !path.startsWith('/administrador/'))) { location.replace(home(me.role)); return; }
  shell(me);
  const page = path.startsWith('/portal-prestamos/') ? 'portal' : path.startsWith('/crear-prestamo/') ? 'form' : path.startsWith('/detalle-prestamo/') ? 'detail' : path.startsWith('/wallet/') ? 'wallet' : path.startsWith('/administrador/') ? 'admin' : path.startsWith('/op/') ? 'return' : null;
  if (!page) throw new Error('No encontramos esta página.');
  const { init } = await import(`./pages/${page}.js`);
  await init(me);
  if (main) main.hidden = false;
  notifications(me).catch(error => errorMessage(error));
}
start().catch(error => {
  const main = $('main');
  if (main && !location.pathname.startsWith('/autenticacion/')) {
    main.replaceChildren(el('h1', '', 'No pudimos cargar esta vista'));
    const retry = el('button', 'button button-primary', 'Reintentar'); retry.onclick = () => location.reload();
    const back = el('a', 'button button-detail', 'Volver'); back.href = '/autenticacion/index.html';
    main.append(retry, back); main.hidden = false;
  }
  errorMessage(error);
}).finally(() => {
  $('#app-loading')?.remove();
  if ($('.app-shell')) $('.app-shell').inert = false;
});
