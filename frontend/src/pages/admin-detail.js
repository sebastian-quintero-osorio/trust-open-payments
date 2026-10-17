import { api, allPages } from '../lib/api.js';
import { $, el, text, show, errorMessage } from '../lib/ui.js';
import { money, date, percent, name, initials, loanState, loanTone, deliveryState, sumMoney } from '../lib/format.js';

export async function init() {
  const query = new URLSearchParams(location.search), id = query.get('id'); query.delete('id'); $('#back-loans').href = `prestamos.html?${query}`;
  let loan;
  try {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id || '')) throw { status: 404 };
    loan = await api('getLoan', { params: { loan_id: id } });
  } catch (error) {
    if (error.status !== 404) throw error;
    show('#loan-not-found'); text('#loan-not-found p', 'No encontramos este préstamo. Vuelve al listado para seleccionar un registro disponible.'); return;
  }
  const realized = ['active', 'paid'].includes(loan.status), root = $('#loan-detail');
  const tone = loanTone(loan), heading = el('div', 'page-heading detail-heading'), identity = el('div'), reference = el('div', 'detail-reference');
  reference.append(el('span', '', loan.id), el('span', 'scope-marker', 'Solo consulta'));
  identity.append(reference, el('h1', '', 'Detalle del préstamo'), el('p', '', `${loan.origin === 'request_funded' ? 'Originado en una solicitud de crédito' : loan.origin === 'match' ? 'Originado en condiciones compatibles' : 'Originado en una oferta de financiación'} · Creado el ${date(loan.created_at)}`));
  const stage = el('div', 'detail-stage'); stage.append(el('span', 'loan-type', realized ? 'Realizado' : loan.origin === 'request_funded' ? 'Solicitado' : 'Ofrecido'), el('span', `loan-badge ${tone}`, loanState(loan)));
  heading.append(identity, stage); root.append(heading);
  const note = el('section', `loan-status-note ${tone}`), noteCopy = el('div'); note.setAttribute('aria-label', 'Situación del préstamo');
  noteCopy.append(el('h2', '', loanState(loan)), el('p', '', realized ? `El ${percent(loan.pct_bps)} de cada venta se destina al crédito.${loan.is_late ? ` Hay ${loan.missed_periods} períodos incumplidos.` : ''}` : 'Aún no hay un desembolso confirmado para este acuerdo.'));
  note.append(noteCopy); root.append(note);
  function section(title, description, className) {
    const panel = el('section', `panel ${className}`), head = el('div', 'section-heading'), copy = el('div');
    copy.append(el('h2', '', title)); if (description) copy.append(el('p', '', description)); head.append(copy); panel.append(head); root.append(panel); return panel;
  }
  function facts(entries, className) {
    const dl = el('dl', className);
    for (const [label, value] of entries) { const group = el('div'); group.append(el('dt', '', label), el('dd', '', value)); dl.append(group); }
    return dl;
  }
  const agreement = el('section', 'loan-agreement'), amount = el('div', 'agreement-amount');
  amount.append(el('h2', '', realized ? 'Capital desembolsado' : 'Capital acordado'), el('strong', '', money(loan.terms.principal)), el('span', '', loan.terms.principal.asset_code), el('p', '', `Total a pagar: ${money(loan.terms.total_repayable, true)}.`));
  const terms = el('div', 'agreement-terms'); terms.append(el('h2', '', 'Condiciones acordadas'), facts([['Cargo fijo sobre el capital', percent(loan.terms.fee_bps)], ['Plazo', `${loan.terms.term_months} meses`], ['Aporte de las ventas', percent(loan.pct_bps)]], ''), el('p', '', `Cargo: ${money(loan.terms.fee_amount, true)}. Mínimo mensual: ${money(loan.terms.monthly_minimum, true)}. El cargo y el aporte de las ventas son conceptos distintos.`));
  agreement.append(amount, terms); root.append(agreement);
  const parties = section('Las partes del préstamo', loan.accepted_at ? `Aceptación del comercio: ${date(loan.accepted_at)}` : 'Participantes del acuerdo', 'participants-panel'), grid = el('div', 'loan-party-grid');
  for (const [label, party] of [['Prestatario', loan.merchant], ['Prestamista', loan.lender]]) {
    const node = el('div', 'loan-party'), head = el('div', 'party-heading'), copy = el('div');
    copy.append(el('span', '', label), el('h3', '', name(party))); head.append(el('span', 'party-avatar', initials(name(party))), copy);
    node.append(head, facts([['Referencia', party.id], ...(party.city ? [['Ubicación', party.city]] : [])], 'party-facts')); grid.append(node);
  }
  parties.append(grid);
  if (realized) {
    const payment = section('Pago del crédito', 'Acumulado desde el desembolso', 'repayment-panel'), figures = el('div', 'repayment-figures');
    const badge = el('span', `loan-badge ${tone}`, `${percent(loan.pct_paid_bps)} del total abonado`); $('.section-heading', payment).append(badge);
    for (const [label, value] of [['Total a pagar', loan.terms.total_repayable], ['Abonado al crédito', loan.allocated], ['Saldo pendiente', loan.outstanding]]) { const group = el('div'); group.append(el('span', '', label), el('strong', '', money(value))); figures.append(group); }
    const progress = el('progress', 'repayment-progress'); progress.max = 10000; progress.value = loan.pct_paid_bps; progress.setAttribute('aria-label', 'Avance del pago');
    const foot = el('div', 'repayment-foot'); foot.append(el('span', '', `Entregado: ${money(loan.delivered, true)}`), el('span', '', loan.period ? `Revisión: ${date(loan.period.next_review_at)}` : `Desembolso: ${date(loan.activated_at)}`));
    payment.append(figures, progress, foot);
    const repayments = await allPages('listRepayments', { query: { loan_id: loan.id } });
    const distribution = section('Así se distribuyeron las ventas', 'Ventas vinculadas a los abonos de este crédito', 'distribution-panel');
    const layout = el('div', 'loan-distribution'), origin = el('div', 'sales-origin'), destinations = el('div', 'distribution-destinations');
    const salesAmount = el('strong', '', '—'); origin.append(el('span', '', 'Ventas recaudadas'), salesAmount, el('small', '', `${loan.terms.principal.asset_code} · Desde el desembolso`));
    const amounts = {};
    for (const [key, label, hint] of [['merchant', 'Para el negocio', 'Después del aporte al crédito'], ['credit', 'Para pagar el crédito', 'Incluye el neto del prestamista y la parte de Trust']]) {
      const row = el('div'), labelNode = el('span', '', label); labelNode.append(el('small', '', hint)); amounts[key] = el('strong', '', '—'); row.append(labelNode, amounts[key]); destinations.append(row);
    }
    const distributionNote = el('p', 'distribution-note', 'Consultando las ventas que originaron los abonos…');
    layout.append(origin, destinations); distribution.append(layout, distributionNote);
    try {
      const traces = [], queue = [...repayments];
      async function worker() {
        while (queue.length) {
          const repayment = queue.shift(), trace = await api('getRepaymentTrace', { params: { repayment_id: repayment.id } });
          if (trace.loan.id !== loan.id) throw new Error('La traza recibida no corresponde a este préstamo.');
          traces.push(trace);
        }
      }
      await Promise.all(Array.from({ length: Math.min(4, queue.length) }, worker));
      const unique = [...new Map(traces.map(trace => [trace.sale.id, trace])).values()];
      const sales = sumMoney(unique.map(trace => trace.sale.amount), loan.terms.principal);
      const gross = sumMoney(unique.map(trace => trace.repayment.abono), loan.terms.principal);
      const merchant = { ...sales, value: (BigInt(sales.value) - BigInt(gross.value)).toString() };
      if (BigInt(merchant.value) < 0n) throw new Error('Los abonos no coinciden con las ventas registradas.');
      salesAmount.textContent = money(sales); amounts.merchant.textContent = money(merchant); amounts.credit.textContent = money(gross);
      distributionNote.textContent = `Distribución asignada: ${money(gross, true)} al crédito. Los estados de entrega se consultan en los recaudos. El porcentaje puede cambiar entre ventas.`;
    } catch (error) { distributionNote.textContent = `No se pudo consultar la distribución: ${error.message}`; distributionNote.setAttribute('role', 'alert'); }
    // A repayment contains the gross credit allocation and the net lender/Trust
    // amounts, not a principal/interest breakdown. Never infer that breakdown.
    const payments = section('Recaudos del préstamo', 'Abonos registrados y estado de entrega', 'payments-panel');
    $('.section-heading', payments).append(el('span', 'scope-marker', `${repayments.length} abonos`));
    const scroll = el('div', 'table-scroll'); scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Abonos del préstamo, tabla desplazable');
    const table = el('table', 'payments-table'), thead = el('thead'), header = el('tr'), tbody = el('tbody');
    for (const label of ['Fecha / referencia', 'Neto prestamista', 'Parte Trust', 'Total al crédito']) { const th = el('th', '', label); th.scope = 'col'; header.append(th); } thead.append(header);
    for (const repayment of [...repayments].sort((a, b) => b.paid_at.localeCompare(a.paid_at))) {
      const row = el('tr'), th = el('th', '', date(repayment.paid_at)); th.scope = 'row'; th.append(el('small', '', repayment.id), el('small', '', deliveryState(repayment.delivery.status)));
      row.append(th, el('td', '', money(repayment.lender_net)), el('td', '', money(repayment.trust_cut)), el('td', '', money(repayment.abono))); tbody.append(row);
    }
    if (!repayments.length) { const row = el('tr'), cell = el('td', '', 'Aún no hay abonos de este préstamo.'); cell.colSpan = 4; row.append(cell); tbody.append(row); }
    const tfoot = el('tfoot'), total = el('tr'); total.append(el('th', '', 'Total registrado'));
    for (const key of ['lender_net', 'trust_cut', 'abono']) total.append(el('td', '', money(sumMoney(repayments.map(r => r[key]), loan.terms.principal)))); tfoot.append(total);
    table.append(el('caption', 'sr-only', 'Abonos brutos y distribución entre el prestamista y Trust'), thead, tbody, tfoot); scroll.append(table); payments.append(scroll);
  } else {
    section('Aún no hay movimientos de este préstamo', 'Sin desembolso confirmado, las condiciones no representan un crédito desembolsado.', 'unfunded-panel');
  }
  const timeline = section('Recorrido del préstamo', 'Hitos confirmados por Trust', 'timeline-panel'), events = el('ol', 'loan-timeline');
  for (const [label, value, explanation] of [
    ['Acuerdo creado', loan.created_at, 'Se registraron las condiciones del acuerdo.'],
    ['Aceptado por el comercio', loan.accepted_at, 'El comercio aceptó las condiciones.'],
    ['Desembolso confirmado', loan.activated_at, `${money(loan.terms.principal, true)} transferidos al negocio.`],
    ['Préstamo pagado', loan.paid_at, 'Se completó el pago del crédito.'],
  ]) if (value) {
    const li = el('li'), time = el('time', '', date(value)), copy = el('div'); time.dateTime = value; copy.append(el('h3', '', label), el('p', '', explanation)); li.append(time, copy); events.append(li);
  }
  timeline.append(events);
  const history = section('Revisiones del porcentaje de ventas', 'Cambios registrados durante el crédito', 'percentage-history-panel');
  try {
    const result = await api('getLoanPctHistory', { params: { loan_id: loan.id } });
    if (!result.items.length) history.append(el('p', 'contract-note', 'El porcentaje de ventas no ha tenido revisiones.'));
    const list = el('ol', 'loan-timeline');
    for (const change of result.items) {
      const li = el('li'), copy = el('div'); copy.append(el('h3', '', `${percent(change.old_pct_bps)} → ${percent(change.new_pct_bps)}`), el('p', '', `Abonado en el período: ${money(change.allocated_in_period, true)} de ${money(change.target_amount, true)}.`));
      li.append(el('time', '', date(change.created_at)), copy); list.append(li);
    }
    history.append(list);
  } catch (error) {
    const message = el('p', 'app-error'); message.id = 'pct-history-error'; message.setAttribute('role', 'alert'); history.append(message); errorMessage(error, '#pct-history-error');
  }
}
