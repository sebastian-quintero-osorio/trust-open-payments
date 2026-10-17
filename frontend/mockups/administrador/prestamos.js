(() => {
  const D = window.TrustAdminLoans;
  const $ = selector => document.querySelector(selector);
  const pageSize = 6;
  let filter = D.filters(new URLSearchParams(location.search));
  let filtered = [];
  const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

  function renderOverview() {
    const completed = D.records.filter(record => record.type === 'realizado');
    const pending = completed.reduce((sum, record) => sum + D.totals(record).pending, 0);
    $('#loan-overview').innerHTML = `<div><span>Registros en la muestra</span><strong>${D.records.length}</strong><small>Todas las etapas</small></div><div><span>Sin desembolso</span><strong>${D.records.length - completed.length}</strong><small>Ofertas y solicitudes</small></div><div><span>Préstamos realizados</span><strong>${completed.length}</strong><small>${completed.filter(record => record.state !== 'pagado').length} activos · ${completed.filter(record => record.state === 'pagado').length} pagado</small></div><div><span>Capital pendiente</span><strong>${D.money(pending)}</strong><small>Solo realizados · COP</small></div>`;
    document.querySelectorAll('[data-type]').forEach(button => {
      button.querySelector('span').textContent = button.dataset.type === 'todos' ? D.records.length : D.records.filter(record => record.type === button.dataset.type).length;
    });
  }

  function controls() {
    document.querySelectorAll('[data-type]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.type === filter.type)));
    $('#loan-search').value = filter.query;
    $('#loan-sort').value = filter.order;
    $('#loan-state').innerHTML = '<option value="todos">Todos los estados</option>' + Object.entries(D.states)
      .filter(([, state]) => filter.type === 'todos' || state.types.includes(filter.type))
      .map(([key, state]) => `<option value="${key}">${state.label}</option>`).join('');
    $('#loan-state').value = filter.state;
  }

  function render() {
    const query = normalize(filter.query.trim());
    filtered = D.records.filter(record => (filter.type === 'todos' || record.type === filter.type)
      && (filter.state === 'todos' || record.state === filter.state)
      && normalize(`${record.id} ${D.name(record.lender)} ${D.name(record.borrower)}`).includes(query));
    filtered.sort((a, b) => {
      if (filter.order === 'monto_mayor') return b.amount - a.amount || a.id.localeCompare(b.id);
      if (filter.order === 'monto_menor') return a.amount - b.amount || a.id.localeCompare(b.id);
      return (filter.order === 'antiguos' ? a.created.localeCompare(b.created) : b.created.localeCompare(a.created)) || a.id.localeCompare(b.id);
    });
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    filter.page = Math.min(filter.page, pages);
    const start = (filter.page - 1) * pageSize;
    const queryString = D.queryString(filter);
    // Some browsers restrict history for file:// pages; filtering still works there.
    try { history.replaceState(null, '', location.pathname + (queryString ? '?' + queryString : '')); } catch { /* Keep local navigation usable. */ }
    $('#loan-rows').innerHTML = filtered.slice(start, start + pageSize).map(record => {
      const detailParams = new URLSearchParams(queryString);
      detailParams.set('id', record.id);
      const origin = record.type === 'realizado' ? (record.origin === 'ofrecido' ? 'Desde una oferta' : 'Desde una solicitud') : (record.type === 'ofrecido' ? 'Oferta de financiación' : 'Solicitud de crédito');
      return `<tr><th scope="row"><span class="loan-type">${D.types[record.type]}</span><strong class="loan-reference">${record.id}</strong><small>${D.date(record.created)}</small><small>${origin}</small></th><td class="loan-parties"><span class="participant-label">Prestatario</span><strong>${D.name(record.borrower)}</strong><span class="participant-label">Prestamista</span><span>${D.name(record.lender)}</span></td><td class="loan-conditions"><span class="mobile-field">Monto en COP</span><strong>${D.amount(record)}</strong><small>${D.number(record.rate)} % E.A. · ${record.term} meses</small><small>${record.share} % de las ventas${record.min !== record.amount && record.type !== 'realizado' ? ' · Rango propuesto' : ''}</small></td><td class="loan-state-cell">${D.badge(record)}<small>${record.type === 'realizado' ? `Capital pendiente<br>${D.money(D.totals(record).pending)}` : record.state === 'aceptado' ? 'Ambas partes aceptaron' : record.state === 'pendiente' ? `Responde el ${record.waiting === 'lender' ? 'prestamista' : 'prestatario'}` : 'Sin desembolso'}</small></td><td class="loan-action"><a class="button button-detail" href="prestamo.html?${D.escape(detailParams.toString())}" aria-label="Ver detalle de ${record.id}">Ver detalle</a></td></tr>`;
    }).join('');
    $('#result-count').textContent = `${filtered.length} ${filtered.length === 1 ? 'registro encontrado' : 'registros encontrados'}${filter.query.trim() ? ` para «${filter.query.trim()}»` : ''}`;
    $('#loan-empty').hidden = filtered.length > 0;
    $('#loan-table-wrap').hidden = !filtered.length;
    $('#loan-pagination').hidden = !filtered.length;
    $('#page-summary').textContent = `${start + 1}–${Math.min(start + pageSize, filtered.length)} de ${filtered.length} registros`;
    $('#page-number').textContent = `${filter.page} / ${pages}`;
    $('#previous-page').disabled = filter.page === 1;
    $('#next-page').disabled = filter.page === pages;
    $('#export-loans').disabled = !filtered.length;
  }

  function reset() {
    filter = D.filters(new URLSearchParams());
    controls();
    render();
    $('#loan-search').focus();
  }
  document.querySelectorAll('[data-type]').forEach(button => button.addEventListener('click', () => {
    filter.type = button.dataset.type;
    filter.state = 'todos';
    filter.page = 1;
    controls();
    render();
  }));
  $('#loan-filters').addEventListener('submit', event => event.preventDefault());
  $('#loan-search').addEventListener('input', event => { filter.query = event.target.value; filter.page = 1; render(); });
  $('#loan-state').addEventListener('change', event => { filter.state = event.target.value; filter.page = 1; render(); });
  $('#loan-sort').addEventListener('change', event => { filter.order = event.target.value; filter.page = 1; render(); });
  $('#clear-filters').addEventListener('click', reset);
  $('#empty-reset').addEventListener('click', reset);
  [ ['#previous-page', -1], ['#next-page', 1] ].forEach(([selector, direction]) => $(selector).addEventListener('click', () => {
    filter.page += direction;
    render();
    $('#list-title').setAttribute('tabindex', '-1');
    $('#list-title').focus();
  }));
  window.addEventListener('popstate', () => { filter = D.filters(new URLSearchParams(location.search)); controls(); render(); });
  $('#export-loans').addEventListener('click', () => {
    const rows = [['ID', 'Etapa', 'Origen', 'Estado', 'Prestatario', 'Prestamista', 'Monto mínimo COP', 'Monto máximo o desembolsado COP', 'Tasa EA %', 'Plazo meses', 'Ventas al crédito %', 'Capital pendiente COP', 'Creación', 'Desembolso', 'Corte', 'Fuente'], ...filtered.map(record => [record.id, D.types[record.type], D.types[record.origin || record.type], D.states[record.state].label, D.name(record.borrower), D.name(record.lender), record.min || record.amount, record.amount, record.rate, record.term, record.share, record.type === 'realizado' ? D.totals(record).pending : '', record.created, record.funded || '', '2026-09-30', 'Datos ficticios · Mockup 09'])];
    const csv = '\uFEFF' + rows.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(';')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'trust-prestamos-muestra-2026-09-30.csv';
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $('#preview-message').textContent = `Se exportaron los ${filtered.length} registros que coinciden con los filtros, incluidas todas las páginas. Datos de demostración en COP.`;
    $('#preview-notice').hidden = false;
  });
  renderOverview();
  controls();
  render();
})();
