(() => {
  const D = TrustLoans, $ = selector => document.querySelector(selector);
  const id = D.currentAccount(document.body.dataset.profile), account = D.accounts[id];
  const borrower = account.role === 'prestatario';
  const baseline = ['esquina', 'aliado'].includes(id);
  let filter = 'all', hiddenBalance = false, currentBalance, movements = [];
  const walletId = `PTD-DEMO-${id.toUpperCase()}`;
  const put = (selector, value) => { $(selector).textContent = value; };
  const node = (tag, value, cls) => { const el = document.createElement(tag); if (value !== undefined) el.textContent = value; if (cls) el.className = cls; return el; };
  document.querySelectorAll('[data-account-name]').forEach(el => { el.textContent = account.name; });
  put('.account strong', account.name);
  put('.account .avatar', account.name.split(' ').map(s => s[0]).slice(0, 2).join(''));
  put('#wallet-id', walletId);
  put('#qr-reference', `TRUST-DEMO-${id.toUpperCase()}`);
  $('#payment-qr').src = `assets/qr-${id}.svg`;
  $('#payment-qr').alt = `QR de demostración de ${account.name}; no permite pagos reales`;
  document.querySelectorAll('[data-portal]').forEach(a => { a.href = D.portal(id); });
  document.querySelectorAll('[data-history]').forEach(a => { a.href = D.portal(id, true); });
  $('#borrower-score').hidden = !borrower;
  $('#lender-summary').hidden = borrower;
  $('#sales-distribution').hidden = !borrower || !baseline;
  if (!borrower) {
    put('#wallet-intro', 'Tu dinero disponible y el retorno de tu financiación.');
    put('#balance-caption', 'Dinero disponible para financiar nuevos préstamos.');
    put('#qr-title', 'Tu QR para recibir');
    put('.qr-instruction', 'Recibe transferencias con Bre-B');
    put('#metrics-title', 'Así se mueve tu capital');
    put('#metrics-caption', 'Capital acumulado y recaudos de hoy · demostración');
  }
  if (borrower && !baseline) {
    put('#score-value', '685');
    $('.score-ring').style.background = 'conic-gradient(from 225deg, #b499fe 0deg 185deg, #3c3348 185deg 270deg, transparent 270deg)';
  }
  const external = $('#test-wallet'), testDialog = $('#test-wallet-dialog');
  try {
    const url = new URL(window.TrustWalletConfig?.testWalletUrl);
    if (url.protocol === 'https:' && !url.username && !url.password) {
      external.href = url.href; external.target = '_blank'; external.rel = 'noopener noreferrer';
      external.removeAttribute('aria-haspopup');
      put('#test-wallet-caption', 'Enlace externo · abre en otra pestaña');
    }
  } catch { /* La URL todavía no existe; el botón explica el estado. */ }
  external.addEventListener('click', event => { if (!external.target) { event.preventDefault(); testDialog.showModal(); } });
  ['#close-test-wallet', '#dismiss-test-wallet'].forEach(s => $(s).addEventListener('click', () => testDialog.close()));
  $('#print-qr').addEventListener('click', () => window.print());
  $('#copy-wallet').addEventListener('click', async () => {
    let message;
    try { await navigator.clipboard.writeText(walletId); message = 'ID de wallet copiado.'; }
    catch { message = `Puedes copiar el ID de wallet: ${walletId}`; }
    put('#preview-message', message); $('#preview-notice').hidden = false;
  });
  function renderBalance() {
    put('#wallet-balance', currentBalance === undefined ? '—' : hiddenBalance ? '••••••' : D.money(currentBalance));
    $('#toggle-balance').setAttribute('aria-label', hiddenBalance ? 'Mostrar saldo' : 'Ocultar saldo');
    $('#toggle-balance').setAttribute('aria-pressed', String(hiddenBalance));
  }
  $('#toggle-balance').addEventListener('click', () => { hiddenBalance = !hiddenBalance; renderBalance(); });
  function renderMovements() {
    const visible = movements.filter(m => filter === 'all' || (filter === 'in' ? m.amount > 0 : m.amount < 0));
    $('#movement-rows').replaceChildren();
    visible.forEach(m => {
      const row = node('tr'), description = node('td');
      const title = m.href ? node('a', m.title) : node('strong', m.title);
      if (m.href) title.href = m.href;
      description.append(title, node('small', m.subtitle));
      const state = node('td'); state.append(node('span', 'Completado', 'movement-state'));
      row.append(description, node('td', m.date), state, node('td', `${m.amount > 0 ? '+' : '−'}${D.money(Math.abs(m.amount))}`, m.amount > 0 ? 'incoming' : 'outgoing'));
      $('#movement-rows').append(row);
    });
    $('#movement-empty').hidden = visible.length !== 0;
    $('.movement-table-wrap').hidden = visible.length === 0;
    put('#movement-count', `${visible.length} ${visible.length === 1 ? 'movimiento' : 'movimientos'}`);
    document.querySelectorAll('[data-movement-filter]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.movementFilter === filter)));
  }
  document.querySelectorAll('[data-movement-filter]').forEach(b => b.addEventListener('click', () => { filter = b.dataset.movementFilter; renderMovements(); }));
  $('#reset-movements').addEventListener('click', () => { filter = 'all'; renderMovements(); });
  function refresh() {
    try {
      const data = D.read();
      const signed = data.filter(t => t.status === 'signed' && (t.borrower === id || t.lender === id));
      const capital = signed.reduce((sum, t) => sum + D.listings[t.listing].amount, 0);
      currentBalance = D.balance(id, data); renderBalance();
      if (borrower) put('#wallet-credit', D.money(D.credit(id, data)));
      const metrics = borrower ? [
        ['Ventas recibidas', D.money(baseline ? 500000 : 0), baseline ? '3 cobros por QR · hoy' : 'Sin cobros de ejemplo'],
        ['Abonado a tus créditos', D.money(baseline ? 50000 : 0), 'Desde tus ventas · hoy'],
        ['Para tu negocio', D.money(baseline ? 450000 : 0), 'Después del aporte al crédito']
      ] : [
        ['Capital desembolsado', D.money((baseline ? 8000000 : 0) + capital), 'Acumulado en préstamos activos'],
        ['Recaudado hoy', D.money(baseline ? 50000 : 0), 'Aportes de ventas de comercios'],
        ['Préstamos activos', String((baseline ? 2 : 0) + signed.length), 'Firmados por ambas partes']
      ];
      $('#wallet-metrics').replaceChildren(...metrics.map(([label, value, help]) => {
        const item = node('article'); item.append(node('h3', label), node('strong', value), node('small', help)); return item;
      }));
      movements = signed.map(t => ({
        title: borrower ? 'Desembolso recibido' : 'Préstamo desembolsado',
        subtitle: `${t.id} · ${D.accounts[borrower ? t.lender : t.borrower].name}`,
        amount: D.listings[t.listing].amount * (borrower ? 1 : -1),
        date: new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(t.signed)),
        timestamp: Date.parse(t.signed), href: D.detail(t, id)
      }));
      if (baseline) [200000, 120000, 180000].forEach((sale, i) => {
        const time = ['14:32', '12:18', '09:45'][i], timestamp = Date.parse(`2026-10-17T${time}:00-05:00`);
        if (borrower) movements.push({ title: 'Venta por QR Bre-B', subtitle: `Venta de ejemplo ${3-i} · Puntored`, amount: sale, date: `17 oct · ${time}`, timestamp });
        movements.push({ title: borrower ? 'Abono automático al crédito' : 'Recaudo de préstamo', subtitle: borrower ? `TR-1048 · 10 % de ${D.money(sale)}` : `La Esquina · venta de ${D.money(sale)}`, amount: sale * .1 * (borrower ? -1 : 1), date: `17 oct · ${time}`, timestamp: timestamp - 1 });
      });
      movements.sort((a,b) => b.timestamp - a.timestamp);
      renderMovements();
      $('#wallet-error').hidden = true;
      $('#wallet-content').hidden = false;
      $('.wallet-activity').hidden = false;
      $('.movements-section').hidden = false;
    } catch (error) {
      currentBalance = undefined; renderBalance();
      put('#wallet-error', error.message); $('#wallet-error').hidden = false;
      $('#wallet-content').hidden = true;
      $('.wallet-activity').hidden = true;
      $('.movements-section').hidden = true;
    }
  }
  window.addEventListener('storage', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('trust-loans-change', refresh);
  refresh();
})();
