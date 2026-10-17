import { api, allPages } from '../lib/api.js';
import { $, $$, el, text, show, busy } from '../lib/ui.js';
import { money, date, percent, name, loanState, loanTone, searchText } from '../lib/format.js';
import { downloadCSV } from '../lib/csv.js';

const realized = loan => ['active', 'paid'].includes(loan.status);
const stage = loan => realized(loan) ? 'realizado' : loan.origin === 'request_funded' ? 'solicitado' : 'ofrecido';
const stages = { realizado: 'Realizado', solicitado: 'Solicitado', ofrecido: 'Ofrecido' };
export async function init() {
  text('.workspace-footer p', 'Consulta de la operación de Trust.');
  if (location.pathname.endsWith('prestamos.html')) return list();
  if (location.pathname.endsWith('prestamo.html')) return (await import('./admin-detail.js')).init();
  return (await import('./admin-dashboard.js')).init();
}
async function list() {
  const loans = await allPages('listLoans');
  const details = new Map();
  async function fillTerms(loan, conditions, outstanding) {
    conditions.textContent = 'Consultando condiciones…';
    conditions.removeAttribute('title');
    try {
      if (!details.has(loan.id)) details.set(loan.id, api('getLoan', { params: { loan_id: loan.id } }).catch(error => { details.delete(loan.id); throw error; }));
      const detail = await details.get(loan.id);
      conditions.textContent = `${percent(detail.terms.fee_bps)} cargo fijo · ${detail.terms.term_months} meses`;
      if (realized(loan)) outstanding.textContent = `Saldo pendiente ${money(detail.outstanding)}`;
    } catch (error) {
      conditions.textContent = 'No se pudieron cargar las condiciones. ';
      if (realized(loan)) outstanding.textContent = 'Saldo pendiente no disponible';
      const retry = el('button', 'text-button', 'Reintentar'); retry.type = 'button'; retry.setAttribute('aria-label', `Reintentar condiciones de ${loan.id}`);
      retry.onclick = () => busy(retry, () => fillTerms(loan, conditions, outstanding)); conditions.append(retry);
      conditions.title = error.message;
    }
  }
  text('.loan-scope p', 'Créditos registrados en toda la plataforma');
  text('.page-heading p', 'Acuerdos pendientes, créditos activos y pagados.');
  text('.register-note', 'Este registro incluye los acuerdos creados. Las publicaciones abiertas sin acuerdo no están disponibles en esta consulta.');
  const overview = $('#loan-overview');
  for (const [label, value] of [['Acuerdos', loans.length], ['Sin desembolso', loans.filter(l => !realized(l)).length], ['Activos', loans.filter(l => l.status === 'active').length], ['Pagados', loans.filter(l => l.status === 'paid').length]]) {
    const item = el('div'); item.append(el('span', '', label), el('strong', '', value)); overview.append(item);
  }
  for (const value of ['active', 'late', 'paid', 'pending_acceptance', 'pending_disbursement', 'disbursing', 'rejected', 'cancelled', 'expired']) {
    const option = el('option', '', value === 'late' ? 'En retraso' : loanState({ status: value, is_late: false })); option.value = value; $('#loan-state').append(option);
  }
  let filters, filtered = [];
  function restore() {
    const q = new URLSearchParams(location.search);
    filters = { type: ['ofrecido', 'solicitado', 'realizado'].includes(q.get('tipo')) ? q.get('tipo') : 'todos', query: q.get('q') || '', state: q.get('estado') || 'todos', sort: q.get('orden') || 'recientes', page: Math.max(1, Number.parseInt(q.get('pagina')) || 1) };
    $('#loan-search').value = filters.query; $('#loan-state').value = filters.state; $('#loan-sort').value = filters.sort;
    if (!$('#loan-state').value) filters.state = $('#loan-state').value = 'todos';
    if (!$('#loan-sort').value) filters.sort = $('#loan-sort').value = 'recientes';
  }
  function render() {
    filtered = loans.filter(loan => (filters.type === 'todos' || stage(loan) === filters.type) && (filters.state === 'todos' || (filters.state === 'late' ? loan.status === 'active' && loan.is_late : filters.state === 'active' ? loan.status === 'active' && !loan.is_late : loan.status === filters.state)) && searchText(`${loan.id} ${name(loan.merchant)} ${name(loan.lender)}`).includes(searchText(filters.query)));
    filtered.sort((a, b) => {
      if (filters.sort.startsWith('monto_')) { const cmp = BigInt(a.principal.value) < BigInt(b.principal.value) ? -1 : BigInt(a.principal.value) > BigInt(b.principal.value) ? 1 : 0; return cmp * (filters.sort === 'monto_mayor' ? -1 : 1); }
      return a.created_at.localeCompare(b.created_at) * (filters.sort === 'antiguos' ? 1 : -1);
    });
    const pages = Math.max(1, Math.ceil(filtered.length / 6)); filters.page = Math.min(filters.page, pages);
    const params = new URLSearchParams({ tipo: filters.type, q: filters.query, estado: filters.state, orden: filters.sort, pagina: filters.page });
    history.replaceState(null, '', '?' + params);
    $$('[data-type]').forEach(button => { button.setAttribute('aria-pressed', String(button.dataset.type === filters.type)); $('span', button).textContent = button.dataset.type === 'todos' ? loans.length : loans.filter(l => stage(l) === button.dataset.type).length; });
    $('#loan-rows').replaceChildren();
    for (const loan of filtered.slice((filters.page - 1) * 6, filters.page * 6)) {
      const row = el('tr'), th = el('th'); th.scope = 'row'; th.append(el('span', 'loan-type', stages[stage(loan)]), el('strong', 'loan-reference', loan.id), el('small', '', date(loan.created_at)));
      th.append(el('small', '', loan.origin === 'request_funded' ? 'Desde una solicitud' : loan.origin === 'match' ? 'Solicitud y oferta compatibles' : 'Desde una oferta'));
      const parties = el('td', 'loan-parties'); parties.append(el('span', 'participant-label', 'Prestatario'), el('strong', '', name(loan.merchant)), el('span', 'participant-label', 'Prestamista'), el('span', '', name(loan.lender)));
      const conditions = el('td', 'loan-conditions'), financial = el('small', 'loan-financial-terms'); conditions.append(el('span', 'mobile-field', 'Monto en COP'), el('strong', '', money(loan.principal)), financial, el('small', '', `${percent(loan.pct_bps)} de las ventas`));
      const state = el('td', 'loan-state-cell'); state.append(el('span', `loan-badge ${loanTone(loan)}`, loanState(loan)));
      const outstanding = el('small', 'loan-outstanding', realized(loan) ? 'Consultando saldo pendiente…' : loan.next_action.actor ? `Siguiente paso: ${loan.next_action.actor === 'merchant' ? 'prestatario' : 'prestamista'}` : 'Sin desembolso'); state.append(outstanding);
      const action = el('td', 'loan-action'), link = el('a', 'button button-detail', 'Ver detalle'); link.href = `prestamo.html?${params}&id=${encodeURIComponent(loan.id)}`; link.setAttribute('aria-label', `Ver detalle de ${loan.id}`); action.append(link);
      row.append(th, parties, conditions, state, action); $('#loan-rows').append(row);
      fillTerms(loan, financial, outstanding);
    }
    text('#result-count', `${filtered.length} registros encontrados`); text('#page-number', `${filters.page} / ${pages}`); text('#page-summary', `${filtered.length ? (filters.page - 1) * 6 + 1 : 0}–${Math.min(filters.page * 6, filtered.length)} de ${filtered.length} registros`);
    show('#loan-table-wrap', filtered.length > 0); show('#loan-empty', !filtered.length); show('#loan-pagination', filtered.length > 0);
    $('#previous-page').disabled = filters.page === 1; $('#next-page').disabled = filters.page === pages; $('#export-loans').disabled = !filtered.length;
  }
  $('#loan-filters').onsubmit = event => event.preventDefault();
  $$('[data-type]').forEach(button => button.onclick = () => { filters.type = button.dataset.type; filters.state = $('#loan-state').value = 'todos'; filters.page = 1; render(); });
  for (const [selector, key, event] of [['#loan-search', 'query', 'input'], ['#loan-state', 'state', 'change'], ['#loan-sort', 'sort', 'change']]) $(selector).addEventListener(event, () => { filters[key] = $(selector).value; filters.page = 1; render(); });
  for (const [selector, direction] of [['#previous-page', -1], ['#next-page', 1]]) $(selector).onclick = () => { filters.page += direction; render(); };
  const reset = () => { history.replaceState(null, '', location.pathname); restore(); render(); };
  $('#clear-filters').onclick = $('#empty-reset').onclick = reset;
  window.addEventListener('popstate', () => { restore(); render(); });
  $('#export-loans').onclick = () => downloadCSV('trust-prestamos.csv', [['ID', 'Etapa', 'Estado', 'Prestatario', 'Prestamista', 'Capital', 'Ventas al crédito', 'Fecha de creación'], ...filtered.map(l => [l.id, stages[stage(l)], loanState(l), name(l.merchant), name(l.lender), money(l.principal, true), percent(l.pct_bps), l.created_at])]);
  restore(); render();
}
