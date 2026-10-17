import { api, allPages } from '../lib/api.js';
import { $, $$, el, text, show, errorMessage, busy } from '../lib/ui.js';
import { money, parseMoney, percent, date, name, initials, loanState, searchText } from '../lib/format.js';

export async function init(me) {
  const lender = me.role === 'lender';
  const marketOperation = lender ? 'listLoanRequests' : 'listLoanOffers';
  let loans = [], mine = [], market = [], credit, marketFilter = !lender, inboxFilter = 'pending';
  const requestedInbox = new URLSearchParams(location.search).get('pendientes');
  if (['pending', 'waiting', 'resolved'].includes(requestedInbox)) inboxFilter = requestedInbox;
  const details = new Map(), requestedDetails = new Set();
  const rowTemplate = $('.offer-row').cloneNode(true);
  $$('.offer-row, .history-table tbody tr').forEach(node => node.remove());
  show('#demo-state-error', false);
  text('#offer-count', '0');
  if (!lender) text('.credit-amount', '—');
  $$('[data-lender-performance]').forEach(panel => {
    text('[data-earned]', '—'); text('[data-average-rate]', '—');
    text('[data-performance-note]', 'Las ganancias históricas y la tasa efectiva anual no están disponibles.');
    const annotation = $('.lender-performance-heading > span', panel); if (annotation) annotation.textContent = 'Histórico';
  });
  text('.offers-heading p', lender ? 'Compara montos, cupo y porcentajes de ventas propuestos.' : 'Compara montos, cargos y aportes de tus ventas.');
  text('.list-labels span:nth-child(3)', lender ? 'Cupo disponible' : 'Cargo fijo');
  text('.offers-footnote p', 'El cargo fijo se calcula sobre el capital. El porcentaje de cada venta se destina al pago del crédito. Revisa las condiciones completas antes de aceptar.');
  text('.credit-explanation p', lender ? 'Define un rango de monto, un cargo fijo y el porcentaje de ventas para el pago.' : 'Una parte de cada venta se destina al pago de tu crédito, según el porcentaje acordado.');
  if (lender) {
    const filters = el('div', 'filter-group'); filters.setAttribute('aria-label', 'Filtrar por mi rango');
    for (const [value, label] of [['eligible', 'Dentro de mi rango'], ['all', 'Todas']]) {
      const button = el('button', '', label); button.type = 'button'; button.dataset.filter = value; button.setAttribute('aria-pressed', String(value === 'all')); filters.append(button);
    }
    $('.offers-heading').append(filters);
    const edit = el('button', 'button button-detail', 'Mi rango'); edit.type = 'button'; $('.credit-summary').append(edit);
    const dialog = el('dialog'), title = el('h2', '', 'Mi rango para prestar'), form = el('form'), save = el('button', 'button button-primary', 'Guardar rango'), clear = el('button', 'button button-detail', 'Quitar rango'), close = el('button', 'button button-detail', 'Cerrar');
    title.id = 'range-title'; dialog.setAttribute('aria-labelledby', title.id); save.type = 'submit'; clear.type = close.type = 'button';
    const error = el('p', 'app-error'); error.id = 'range-error'; error.hidden = true; error.setAttribute('role', 'alert');
    for (const [key, label] of [['min', 'Monto mínimo (COP)'], ['max', 'Monto máximo (COP)']]) {
      const field = el('label', 'app-field', label), input = el('input'); input.id = `range-${key}`; input.required = true; input.inputMode = 'decimal'; input.value = me.lender_range ? money(me.lender_range[key]).replace('$', '') : ''; field.append(input); form.append(field);
    }
    form.append(error, save, clear); dialog.append(title, form, close); document.body.append(dialog); edit.onclick = () => dialog.showModal(); close.onclick = () => dialog.close();
    form.onsubmit = event => { event.preventDefault(); busy(save, async () => {
      error.hidden = true;
      const min = parseMoney($('#range-min').value), max = parseMoney($('#range-max').value);
      if (BigInt(min.value) <= 0n || BigInt(max.value) < BigInt(min.value)) throw new Error('El mínimo debe ser positivo y no superar el máximo.');
      await api('updateMe', { body: { lender_range: { min, max } } }); dialog.close(); await loadMarket();
    }, '#range-error'); };
    clear.onclick = () => busy(clear, async () => { await api('updateMe', { body: { lender_range: null } }); $('#range-min').value = $('#range-max').value = ''; dialog.close(); await loadMarket(); }, '#range-error');
  }
  const tabs = $$('[role="tab"][data-view]');
  function activate() {
    const view = ['historial', 'recibidas'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'explorar';
    document.body.dataset.view = view;
    tabs.forEach(tab => { const active = tab.dataset.view === view; tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1; show(`#panel-${tab.dataset.view}`, active); });
    if (view !== 'explorar') hydrate(loans);
  }
  tabs.forEach((tab, index) => {
    tab.onclick = () => { location.hash = tab.dataset.view; activate(); };
    tab.onkeydown = event => {
      const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
      if (next === undefined) return; event.preventDefault(); tabs[next].focus(); tabs[next].click();
    };
  });
  window.addEventListener('hashchange', activate); activate();
  function linkFor(item, loan = false) {
    return `/detalle-prestamo/${loan || !lender ? 'oferta' : 'solicitud'}.html?${loan ? 'loan_id' : 'id'}=${encodeURIComponent(item.id)}`;
  }
  async function hydrate(records) {
    const pending = records.filter(record => !requestedDetails.has(record.id));
    pending.forEach(record => requestedDetails.add(record.id));
    let next = 0;
    async function worker() {
      while (next < pending.length) {
        const record = pending[next++];
        try { details.set(record.id, await api('getLoan', { params: { loan_id: record.id } })); }
        catch (error) { errorMessage(error); }
      }
    }
    await Promise.all(Array.from({ length: Math.min(4, pending.length) }, worker));
    if (pending.length) { renderInbox(); renderHistory(); }
  }
  function renderMarket() {
    $$('.offer-row, #market-empty').forEach(node => node.remove());
    text('#offer-count', market.length); text('#filter-status', `${market.length} ${lender ? 'solicitudes' : 'ofertas'}.`);
    if (!market.length) {
      const empty = el('p', 'history-help', 'No hay préstamos disponibles con estos filtros.'); empty.id = 'market-empty'; $('.offer-list').append(empty);
    }
    market.forEach((item, index) => {
      const row = rowTemplate.cloneNode(true); row.hidden = false; row.removeAttribute('data-eligible');
      const party = name(lender ? item.merchant : item.lender);
      $('.lender h3', row).textContent = party; $('.lender h3', row).id = `market-${index}`; row.setAttribute('aria-labelledby', `market-${index}`);
      $('.lender-avatar', row).textContent = initials(party);
      $('.lender p', row).textContent = lender ? item.merchant.city : 'Oferta de préstamo';
      $('.metric-amount strong', row).textContent = item.min_amount.value === item.max_amount.value ? money(item.max_amount) : `${money(item.min_amount)} a ${money(item.max_amount)}`;
      $('.metric-interest .metric-label', row).textContent = lender ? 'Cupo disponible' : 'Cargo fijo';
      $('.metric-interest strong', row).textContent = lender ? money(item.credit?.cupo_disponible) : percent(item.fee_bps);
      $('.metric-interest small', row).textContent = lender ? 'Calculado por Trust' : 'Sobre el capital';
      $('.metric-repayment strong', row).textContent = percent(item.pct_bps);
      const a = $('a', row); a.href = linkFor(item); a.removeAttribute('data-listing'); a.setAttribute('aria-label', `Ver ${lender ? 'solicitud' : 'oferta'} de ${party}`);
      $('.offer-list').append(row);
    });
  }
  let marketSequence = 0;
  async function loadMarket() {
    const sequence = ++marketSequence;
    const result = await allPages(marketOperation, { query: { scope: 'market', ...(lender ? { in_my_range: marketFilter } : { fits_only: marketFilter }) } });
    if (sequence !== marketSequence) return;
    market = result; renderMarket();
  }
  $$('[data-filter]').forEach(button => button.onclick = () => busy(button, async () => {
    marketFilter = button.dataset.filter === 'eligible';
    $$('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    await loadMarket();
  }));
  function renderInbox() {
    $$('[data-received-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.receivedFilter === inboxFilter)));
    const pending = loans.filter(loan => loan.next_action.actor === me.role && loan.next_action.type !== 'none');
    text('#received-count', pending.length); text('#pending-filter-count', pending.length);
    show('#approval-inbox', pending.length > 0); text('#inbox-title', `Tienes ${pending.length} acuerdos por responder`);
    text('#approval-inbox p', 'Revisa las condiciones y el siguiente paso de cada acuerdo.');
    const open = ['pending_acceptance', 'pending_disbursement', 'disbursing'];
    const visible = inboxFilter === 'pending' ? pending : inboxFilter === 'waiting' ? loans.filter(l => open.includes(l.status) && l.next_action.actor !== me.role) : loans.filter(l => !open.includes(l.status));
    text('#received-summary', `${visible.length} acuerdos`); $('#inbox-list').replaceChildren(); show('#received-empty', visible.length === 0);
    for (const loan of visible) {
      const other = name(lender ? loan.merchant : loan.lender);
      const card = el('article', 'received-card'), head = el('div', 'received-card-head'), identity = el('div', 'received-identity');
      identity.append(el('h3', '', other), el('p', '', loan.id));
      head.append(el('span', 'lender-avatar', initials(other)), identity, el('time', 'received-time', date(loan.created_at)));
      const terms = el('dl', 'received-terms');
      const full = details.get(loan.id);
      [['Monto', money(loan.principal, true)], ['Cargo fijo', percent(full?.terms.fee_bps)], ['Plazo', full ? `${full.terms.term_months} meses` : '—'], ['De cada venta', percent(loan.pct_bps)]].forEach(([label, value]) => { const div = el('div'); div.append(el('dt', '', label), el('dd', '', value)); terms.append(div); });
      const footer = el('div', 'received-card-foot'), a = el('a', 'button button-detail', 'Revisar acuerdo'); a.href = linkFor(loan, true);
      footer.append(el('span', 'received-status', loanState(loan)), a); card.append(head, terms, footer); $('#inbox-list').append(card);
    }
  }
  $$('[data-received-filter]').forEach(button => button.onclick = () => {
    inboxFilter = button.dataset.receivedFilter;
    $$('[data-received-filter]').forEach(b => b.setAttribute('aria-pressed', String(b === button))); renderInbox();
  });
  function renderHistory() {
    const signed = $('#history-type').value === 'firmado', state = $('#history-state').value;
    const query = searchText($('#history-search').value);
    show('#payment-state-field', signed); $('#history-state').disabled = !signed;
    text('#history-rate-heading', 'Cargo fijo'); text('#history-status-heading', 'Estado');
    text('#history-date-heading', signed ? 'Fecha de activación' : 'Fecha de publicación');
    text('#history-amount-heading', signed ? 'Monto' : 'Rango de monto');
    text('#history-help', 'Al día y En retraso corresponden a créditos con desembolso confirmado.');
    const list = (signed ? loans.filter(l => ['active', 'paid'].includes(l.status)) : mine).filter(item => {
      const party = name(lender ? item.merchant : item.lender);
      const matchesState = state === 'todos' || (state === 'pagado' ? item.status === 'paid' : item.status === 'active' && item.is_late === (state === 'en-retraso'));
      return (!signed || matchesState) && searchText(`${item.id} ${party}`).includes(query);
    });
    const body = $('.history-table tbody'); body.replaceChildren();
    for (const item of list) {
      const row = el('tr'), th = el('th'); th.scope = 'row';
      const a = el('a', 'record-id', item.id);
      // Own offers and requests use their original listing type, not the marketplace type.
      a.href = signed ? linkFor(item, true) : `/detalle-prestamo/${lender ? 'oferta' : 'solicitud'}.html?id=${encodeURIComponent(item.id)}`;
      th.append(a, el('span', 'record-name', signed ? name(lender ? item.merchant : item.lender) : lender ? 'Mi oferta' : 'Mi solicitud')); row.append(th);
      const values = [signed ? money(item.principal, true) : `${money(item.min_amount)} a ${money(item.max_amount)}`, percent(signed ? details.get(item.id)?.terms.fee_bps : item.fee_bps), percent(item.pct_bps), date(signed ? item.activated_at : item.created_at), loanState(item)];
      values.forEach((value, index) => { const td = el('td', '', value); td.dataset.label = ['Monto', 'Cargo fijo', 'De cada venta', 'Fecha', 'Estado'][index]; row.append(td); }); body.append(row);
    }
    text('#history-results', `${list.length} registros`); show('#history-table-container', list.length > 0); show('#history-empty', list.length === 0);
  }
  $('#history-type').addEventListener('change', () => { $('#history-state').value = 'todos'; renderHistory(); });
  ['#history-state', '#history-search'].forEach(selector => $(selector).addEventListener(selector.endsWith('search') ? 'input' : 'change', renderHistory));
  ['#history-reset', '#history-empty-reset'].forEach(selector => $(selector).onclick = () => { $('#history-search').value = ''; $('#history-state').value = 'todos'; renderHistory(); });
  const responses = await Promise.allSettled([
    loadMarket(),
    allPages('listLoans').then(result => { loans = result; renderInbox(); }),
    allPages(lender ? 'listLoanOffers' : 'listLoanRequests', { query: { scope: 'mine' } }).then(result => { mine = result; }),
    !lender ? api('getMyCreditProfile').then(result => { credit = result; const amount = $('.credit-amount'); amount.replaceChildren(document.createTextNode(money(credit.cupo_disponible)), el('span', '', 'COP')); text('.credit-caption', credit.eligible ? 'Para nuevas solicitudes de préstamo.' : credit.reasons.join(' ')); }) : Promise.resolve(),
  ]);
  renderHistory(); responses.forEach(result => { if (result.status === 'rejected') errorMessage(result.reason); });
  if (document.body.dataset.view !== 'explorar') await hydrate(loans);
}
