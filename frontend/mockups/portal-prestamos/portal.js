const isLender = document.body.dataset.profile === 'prestamista';
const portalAccount = TrustLoans.currentAccount(isLender ? 'prestamista' : 'prestatario');
const accountName = TrustLoans.accounts[portalAccount].name;
const allowedHistoryTypes = ['firmado', isLender ? 'ofrecido' : 'solicitado'];
const filterButtons = [...document.querySelectorAll('[data-filter]')];
const offers = [...document.querySelectorAll('.offer-row')];
filterButtons.forEach(button => {
  button.addEventListener('click', () => {
    const eligibleOnly = button.dataset.filter === 'eligible';
    filterButtons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    offers.forEach(offer => { offer.hidden = eligibleOnly && offer.dataset.eligible !== 'true'; });
    const count = offers.filter(offer => !offer.hidden).length;
    document.querySelector('#offer-count').textContent = count;
    document.querySelector('#filter-status').textContent = `${count} ofertas ${eligibleOnly ? 'dentro de tu cupo' : 'en total'}.`;
  });
});

const portalTabs = [...document.querySelectorAll('[data-view]')];
function activateView(view, updateUrl = false) {
  const selected = ['historial', 'recibidas'].includes(view) ? view : 'explorar';
  document.body.dataset.view = selected;
  portalTabs.forEach(tab => {
    const active = tab.dataset.view === selected;
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    document.querySelector(`#panel-${tab.dataset.view}`).hidden = !active;
  });
  if (updateUrl && location.hash !== `#${selected}`) {
    try { history.pushState(null, '', `#${selected}`); } catch { /* La navegación local sigue funcionando sin historial del navegador. */ }
  }
}
portalTabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateView(tab.dataset.view, true));
  tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % portalTabs.length;
    if (event.key === 'ArrowLeft') next = (index + portalTabs.length - 1) % portalTabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = portalTabs.length - 1;
    if (next === undefined) return;
    event.preventDefault();
    portalTabs[next].focus();
    activateView(portalTabs[next].dataset.view, true);
  });
});
const restoreView = () => activateView(location.hash.slice(1));
window.addEventListener('popstate', restoreView);
window.addEventListener('hashchange', restoreView);
restoreView();

const historyType = document.querySelector('#history-type');
const historyState = document.querySelector('#history-state');
const historySearch = document.querySelector('#history-search');
const normalizeSearch = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').trim();
function filterHistory() {
  if (!allowedHistoryTypes.includes(historyType.value)) historyType.value = 'firmado';
  const type = historyType.value;
  const signed = type === 'firmado';
  const query = normalizeSearch(historySearch.value);
  document.querySelector('#payment-state-field').hidden = !signed;
  historyState.disabled = !signed;
  let count = 0;
  document.querySelectorAll('.history-table tbody tr').forEach(row => {
    const matches = row.dataset.kind === type && (!signed || historyState.value === 'todos' || row.dataset.state === historyState.value) && normalizeSearch(row.querySelector('th').textContent).includes(query);
    row.hidden = !matches;
    if (matches) count++;
  });
  const labels = { firmado: ['préstamo firmado', 'préstamos firmados'], ofrecido: ['préstamo ofrecido', 'préstamos ofrecidos'], solicitado: ['préstamo solicitado', 'préstamos solicitados'] };
  document.querySelector('#history-results').textContent = `${count} ${labels[type][count === 1 ? 0 : 1]}`;
  document.querySelector('#history-caption').textContent = `${labels[type][1]} de ${accountName}`;
  document.querySelector('#history-record-heading').textContent = signed ? `Préstamo / ${isLender ? 'prestatario' : 'prestamista'}` : type === 'solicitado' ? 'Solicitud' : 'Oferta';
  document.querySelector('#history-amount-heading').textContent = signed ? 'Monto' : 'Rango de monto';
  document.querySelector('#history-rate-heading').textContent = type === 'solicitado' ? 'Tasa máxima propuesta' : 'Tasa de interés';
  document.querySelector('#history-date-heading').textContent = signed ? 'Fecha de firma' : type === 'solicitado' ? 'Fecha de solicitud' : 'Fecha de oferta';
  document.querySelector('#history-status-heading').textContent = signed ? 'Estado' : 'Situación';
  document.querySelector('#history-table-container').hidden = count === 0;
  document.querySelector('#history-empty').hidden = count !== 0;
  document.querySelector('#history-empty-title').textContent = 'No encontramos registros con esos filtros';
  document.querySelector('#history-empty-message').textContent = 'Prueba con otro nombre, ID o estado del préstamo.';
  document.querySelector('#history-reset').hidden = type === 'firmado' && historyState.value === 'todos' && !query;
  document.querySelector('#history-help').hidden = !signed;
  document.querySelector('#history-help').textContent = signed ? 'Al día, En retraso y Pagado describen únicamente préstamos firmados por ambas partes.' : '';
}
historyType.addEventListener('change', () => { historyState.value = 'todos'; filterHistory(); });
historyState.addEventListener('change', filterHistory);
historySearch.addEventListener('input', filterHistory);
function resetHistory() {
  historyType.value = 'firmado';
  historyState.value = 'todos';
  historySearch.value = '';
  filterHistory();
  historyType.focus();
}
document.querySelector('#history-reset').addEventListener('click', resetHistory);
document.querySelector('#history-empty-reset').addEventListener('click', resetHistory);
filterHistory();
