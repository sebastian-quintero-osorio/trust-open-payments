import { api, allPages } from '../lib/api.js';
import { $, $$, el, text, show, busy, safeExternal, polling } from '../lib/ui.js';
import { money, date, name, deliveryState, payoutLeg } from '../lib/format.js';
import { home } from '../app.js';

export async function init(me) {
  const merchant = me.role === 'merchant';
  let collection = null, balance = null, hidden = false, filter = 'all', movements = [], since = null, config;
  const seenSales = new Map();
  $$('[data-account-name]').forEach(node => node.textContent = me.business_name || me.public_name);
  $$('[data-portal]').forEach(node => node.href = home(me.role));
  show('#borrower-score', merchant); show('#lender-summary', false); show('.qr-section', false); show('#sales-distribution', false);
  text('#metrics-caption', merchant ? 'Ventas confirmadas de hoy' : 'Capital y abonos de tu portafolio'); text('.period-tag', 'Hoy');
  text('.ledger-note', 'Los estados distinguen los importes asignados al crédito de los pagos ya entregados.');
  text('.movement-table caption', 'Operaciones de tu cuenta');
  text('.movement-table th:last-child', 'Monto / moneda');
  text('.wallet-balance small', merchant ? 'COP' : me.payout_wallet?.asset_code || '');
  text('#movements-title', merchant ? 'Ventas y abonos' : 'Abonos de tus préstamos');
  text('.movements-section .section-heading p', 'Consulta el origen y el estado de entrega de cada operación.');
  text('.score-copy > small', 'Evaluación calculada por Trust a partir de tu actividad.');
  text('#wallet-id', me.payout_wallet?.url || 'Sin wallet vinculada');
  text('.wallet-state', me.payout_wallet ? 'Vinculada' : 'Por vincular');
  text('#balance-caption', merchant ? 'Saldo de tu cuenta de recaudo Trust.' : 'El saldo de tu wallet externa se consulta directamente en tu proveedor.');
  const linkForm = el('form', 'form-surface');
  const field = el('label', 'app-field', me.payout_wallet ? 'Cambiar wallet vinculada' : 'Vincular mi wallet');
  const input = el('input'); input.name = 'wallet_address'; input.required = true; input.placeholder = '$proveedor/mi-wallet'; input.value = me.payout_wallet?.url || '';
  field.append(input); const save = el('button', 'button button-detail', 'Vincular wallet'); save.type = 'submit';
  const accountStatus = el('p', 'contract-note'); accountStatus.setAttribute('role', 'status');
  linkForm.append(field, save, accountStatus); $('.wallet-overview').append(linkForm);
  linkForm.onsubmit = event => { event.preventDefault(); busy(save, async () => {
    me = await api('setPayoutWallet', { body: { wallet_address: input.value.trim() } });
    text('#wallet-id', collection?.wallet_address || me.payout_wallet.url); text('.wallet-state', collection ? collection.status === 'active' ? 'Activa' : 'Revocada' : 'Vinculada'); accountStatus.textContent = 'Wallet vinculada.';
    text('.wallet-balance small', merchant ? 'COP' : me.payout_wallet.asset_code);
    if (merchant) activate.hidden = collection?.status === 'active';
  }); };
  const activate = el('button', 'button button-primary', 'Activar mi QR'); activate.type = 'button'; activate.hidden = !merchant || !me.payout_wallet;
  linkForm.append(activate); activate.onclick = () => busy(activate, async () => { collection = await api('createCollectionAccount'); await renderCollection(); });
  $('#copy-wallet').onclick = () => busy($('#copy-wallet'), async () => {
    const id = collection?.wallet_address || me.payout_wallet?.url;
    if (!id) throw new Error('Primero vincula una wallet.');
    try { await navigator.clipboard.writeText(id); accountStatus.textContent = 'Dirección de wallet copiada.'; }
    catch { accountStatus.textContent = `Copia esta dirección: ${id}`; }
  });
  function renderBalance() {
    text('#wallet-balance', hidden ? '••••••' : money(balance));
    $('#toggle-balance').setAttribute('aria-label', hidden ? 'Mostrar saldo' : 'Ocultar saldo'); $('#toggle-balance').setAttribute('aria-pressed', String(hidden));
  }
  $('#toggle-balance').onclick = () => { hidden = !hidden; renderBalance(); };
  const external = $('#test-wallet'), testDialog = $('#test-wallet-dialog');
  if (window.TRUST_CONFIG?.testWalletUrl) {
    external.href = safeExternal(window.TRUST_CONFIG.testWalletUrl); external.target = '_blank'; external.rel = 'noopener noreferrer'; external.removeAttribute('aria-haspopup');
  } else external.onclick = event => { event.preventDefault(); testDialog.showModal(); };
  $('#close-test-wallet').onclick = $('#dismiss-test-wallet').onclick = () => testDialog.close();
  $('#print-qr').onclick = () => window.print();
  async function renderCollection() {
    const active = collection?.status === 'active';
    activate.hidden = active || !me.payout_wallet;
    show('.qr-section', active);
    if (!collection) {
      balance = null; renderBalance(); text('#wallet-id', me.payout_wallet?.url || 'Sin wallet vinculada'); text('.wallet-state', me.payout_wallet ? 'Vinculada' : 'Por vincular'); return;
    }
    balance = collection.balance; renderBalance();
    text('#wallet-id', collection.wallet_address); text('.wallet-state', active ? 'Activa' : 'Revocada');
    if (!active) return;
    if (!window.qrcode) await new Promise((resolve, reject) => {
      const script = document.createElement('script'); script.src = '/vendor/qrcode.js'; script.onload = resolve; script.onerror = () => reject(new Error('No se pudo generar el QR. Recarga la página.')); document.head.append(script);
    });
    if ($('#payment-qr').dataset.payload !== collection.qr_payload) {
      const qr = window.qrcode(0, 'M'); qr.addData(collection.qr_payload); qr.make();
      $('#payment-qr').src = qr.createDataURL(6, 24); $('#payment-qr').dataset.payload = collection.qr_payload;
    }
    $('#payment-qr').alt = `QR para pagar a ${me.business_name}`;
    text('#qr-reference', collection.id); text('.qr-demo', 'Cuenta de recaudo Trust');
    text('.print-copy', 'Escanea el código para abrir la página de pago de tu negocio.');
    text('.qr-help', 'Listo para imprimir o guardar como PDF.');
  }
  function metrics(values) {
    $('#wallet-metrics').replaceChildren(...values.map(([label, value, hint]) => { const node = el('article'); node.append(el('h3', '', label), el('strong', '', value), el('small', '', hint)); return node; }));
  }
  function renderMovements() {
    const visible = movements.filter(item => filter === 'all' || item.direction === filter);
    $('#movement-rows').replaceChildren();
    for (const item of visible) {
      const row = el('tr'), description = el('td'), button = el('button', 'session-logout', item.title);
      button.onclick = () => busy(button, async () => {
        const result = await api(item.kind === 'sale' ? 'getSale' : 'getRepaymentTrace', { params: { [item.kind === 'sale' ? 'sale_id' : 'repayment_id']: item.id } });
        const dialog = el('dialog'), title = el('h2', '', item.title), close = el('button', 'button button-detail', 'Cerrar');
        title.id = 'movement-detail-title'; dialog.setAttribute('aria-labelledby', title.id); close.onclick = () => dialog.close();
        dialog.addEventListener('close', () => dialog.remove(), { once: true });
        dialog.append(title, el('p', '', `Venta: ${money(result.sale.amount, true)} · ${date(result.sale.paid_at)}`));
        if (result.repayment) dialog.append(el('p', '', `Abono: ${money(result.repayment.abono, true)} · Parte de Trust: ${money(result.repayment.trust_cut, true)} · Neto del prestamista: ${money(result.repayment.lender_net, true)}`));
        for (const payout of result.payouts) dialog.append(el('p', '', `${payoutLeg(payout.leg)}: ${money(payout.amount, true)} · ${deliveryState(payout.status)}${payout.receive ? ' · Recibido: ' + money(payout.receive, true) : ''}`));
        dialog.append(close); document.body.append(dialog); dialog.showModal();
      });
      description.append(button, el('small', '', item.id));
      row.append(description, el('td', '', date(item.date)), el('td', '', item.status), el('td', item.direction === 'in' ? 'incoming' : 'outgoing', money(item.amount, true))); $('#movement-rows').append(row);
    }
    text('#movement-count', `${visible.length} movimientos`); show('#movement-empty', !visible.length); show('.movement-table-wrap', Boolean(visible.length));
    $$('[data-movement-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.movementFilter === filter)));
  }
  $$('[data-movement-filter]').forEach(button => button.onclick = () => { filter = button.dataset.movementFilter; renderMovements(); });
  $('#reset-movements').onclick = () => { filter = 'all'; renderMovements(); };
  async function refreshSales() {
    const result = await api('getSalesToday', { query: { since } });
    // Keep earlier days in the historical ledger when Bogotá crosses midnight.
    since = result.next_since;
    for (const row of result.rows) seenSales.set(row.sale_id, row);
    metrics([['Ventas recibidas', money(result.summary.sales_total), `${result.summary.sales_count} cobros de hoy`], ['Abonado a tu crédito', money(result.summary.abonado_total), 'Asignado desde tus ventas'], ['Para tu negocio', money(result.summary.merchant_total), 'Parte de tus ventas']]);
    const example = config.example_splits.find(split => split.pct_bps === result.rule.lender_bps);
    show('#sales-distribution');
    text('#sales-distribution > div > p', example ? `Ejemplo con una venta de ${money(config.example_sale, true)} y el porcentaje vigente.` : 'Distribución vigente de las ventas recibidas.');
    text('.distribution-labels > span:first-child strong', example ? money(example.merchant) : `${result.rule.merchant_bps / 100} %`);
    text('.distribution-labels > span:last-child strong', example ? money(example.abono) : `${result.rule.lender_bps / 100} %`);
    $('.distribution-track > span').style.width = `${result.rule.merchant_bps / 100}%`;
    $('.distribution-track').setAttribute('aria-label', `${result.rule.merchant_bps / 100} por ciento para tu negocio; ${result.rule.lender_bps / 100} por ciento para el crédito`);
    renderSalesRows();
  }
  function renderSalesRows() {
    movements = [...seenSales.values()].sort((a, b) => b.paid_at.localeCompare(a.paid_at)).flatMap(row => [
      { kind: 'sale', id: row.sale_id, title: 'Venta por QR Bre-B', direction: 'in', amount: row.amount, date: row.paid_at, status: deliveryState(row.payout_status) },
      ...(BigInt(row.abono_amount.value) > 0n ? [{ kind: 'sale', id: row.sale_id, title: 'Abono al crédito', direction: 'out', amount: row.abono_amount, date: row.paid_at, status: 'Asignado al crédito' }] : []),
    ]);
    renderMovements();
  }
  function renderCredit(credit) {
    text('#score-value', credit.score); text('#wallet-credit', money(credit.cupo_disponible)); text('.score-heading > span', `Grado ${credit.grade}`);
    $('.score-ring').style.background = `conic-gradient(from 225deg, #b499fe 0deg ${Math.max(0, Math.min(1000, credit.score)) * .27}deg, #3c3348 0deg 270deg, transparent 270deg)`;
  }
  async function readCollection() {
    try { return await api('getCollectionAccount'); } catch (error) { if (error.status !== 404) throw error; return null; }
  }
  function updateStatus(error) {
    text('#wallet-error', error ? `${error.message} La información se actualizará cuando vuelva la conexión.` : ''); show('#wallet-error', Boolean(error));
  }
  if (merchant) {
    const initial = await Promise.all([api('getMyCreditProfile'), readCollection(), api('getConfig'), allPages('listSales')]);
    renderCredit(initial[0]); collection = initial[1]; config = initial[2];
    await renderCollection();
    initial[3].forEach(row => seenSales.set(row.sale_id, row));
    await refreshSales();
    // since only carries new sales. Periodic history refresh is necessary for
    // deliveries which complete later, as well as balance/credit/QR revocation.
    let staleSales = false, staleAccount = false;
    polling(refreshSales, 2500, { onError: error => { staleSales = true; updateStatus(error); }, onSuccess: () => { staleSales = false; if (!staleAccount) updateStatus(null); } });
    polling(async () => {
      const [credit, account, history] = await Promise.all([api('getMyCreditProfile'), readCollection(), allPages('listSales')]);
      renderCredit(credit); collection = account; await renderCollection();
      history.forEach(row => seenSales.set(row.sale_id, row));
      renderSalesRows();
    }, 30000, { onError: error => { staleAccount = true; updateStatus(error); }, onSuccess: () => { staleAccount = false; if (!staleSales) updateStatus(null); } });
  } else {
    show('#lender-summary');
    text('.lender-performance-heading > span', 'Histórico');
    text('[data-earned]', '—'); text('[data-average-rate]', '—');
    text('[data-performance-note]', 'Las ganancias históricas y la tasa efectiva anual no están disponibles en esta consulta.');
    text('#metrics-title', 'Así se mueve tu portafolio');
    async function refreshLender() {
      const [dashboard, repayments] = await Promise.all([api('getLenderDashboard'), allPages('listRepayments')]);
      text('#metrics-caption', `${dashboard.active_loans_count} préstamos activos con desembolso confirmado`);
      const values = dashboard.totals.flatMap(total => [['Capital prestado', money(total.total_prestado, true), 'Acumulado'], ['Abonos de hoy', money(total.abonos_hoy.gross, true), `Parte de Trust: ${money(total.abonos_hoy.trust_cut, true)}`], ['Recibido hoy', money(total.recibido_hoy, true), 'Pagos entregados']]);
      metrics(values);
      movements = repayments.map(row => ({ kind: 'repayment', id: row.id, title: `Abono de ${name(row.merchant)}`, direction: 'in', amount: row.delivery.received || row.lender_net, date: row.paid_at, status: deliveryState(row.delivery.status) }));
      renderMovements();
    }
    await refreshLender();
    polling(refreshLender, 30000, { onError: updateStatus, onSuccess: () => updateStatus(null) });
  }
  renderBalance();
}
