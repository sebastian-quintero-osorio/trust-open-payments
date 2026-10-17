import { api, allPages } from '../lib/api.js';
import { $, $$, el, text } from '../lib/ui.js';
import { money, sumMoney, deliveryState } from '../lib/format.js';
import { downloadCSV } from '../lib/csv.js';

export async function init() {
  const [data, loans, config] = await Promise.all([api('getAdminDashboard'), allPages('listLoans'), api('getConfig')]);
  const funded = loans.filter(loan => loan.activated_at && ['active', 'paid'].includes(loan.status));
  const capital = sumMoney(funded.map(loan => loan.principal), config.market_asset);
  const active = loans.filter(loan => loan.status === 'active'), late = active.filter(loan => loan.is_late).length;
  const finance = [['Ingresos de Trust', data.trust_revenue], ['Por entregar a Trust', data.trust_pending], ['Fondos retenidos', data.retained_funds], ['Pagos en curso', data.in_flight]];
  $('#period').replaceChildren(el('option', '', 'Histórico disponible')); $('#period').disabled = true;
  text('#comparison-label', 'Sin comparación histórica disponible'); $$('.snapshot').forEach(node => node.textContent = 'Consulta actual');
  const cards = [
    ['Préstamos desembolsados', funded.length, 'Créditos con desembolso confirmado', 'Histórico disponible', 'loans'],
    ['Capital desembolsado', money(capital), 'Financiación entregada a negocios', config.market_asset.asset_code, 'funded'],
    ['Préstamos activos', active.length, 'Con saldo pendiente en la consulta', `${active.length - late} al día · ${late} en retraso`, 'active'],
    ['Tasa promedio ponderada', '—', 'Ponderada por capital pendiente', 'La tasa E.A. no está disponible', 'rate'],
  ];
  for (const [label, value, context, change, detail] of cards) {
    const card = el('article', 'kpi'), heading = el('div', 'kpi-label'), info = el('button', 'info-button', 'i');
    info.type = 'button'; info.dataset.detail = detail; info.setAttribute('aria-label', `Definición: ${label}`);
    heading.append(el('span', '', label), info);
    card.append(heading, el('strong', 'kpi-value', value), el('span', 'kpi-context', context), el('span', 'change neutral', change)); $('#kpis').append(card);
  }
  // Keep the mockup's complete hierarchy and spacing even for unavailable data.
  // A dash means unavailable, never zero; bars do not imply invented values.
  text('#sales-total', '—'); text('#sales-change', 'Información no disponible en esta consulta.');
  for (const [label, hint] of [['Pagado a negocios', 'Abonos confirmados después del aporte al crédito'], ['Pagado a prestamistas', 'Capital y cargo cobrados']]) {
    const card = el('div', 'destination'); card.append(el('span', 'destination-label', label), el('strong', '', '—'), el('p', '', hint), el('span', 'destination-share', '—')); $('#destinations').append(card);
  }
  text('#reconciliation', 'La distribución agregada de ventas aún no está disponible.'); text('.reconciled-icon', '—'); text('.reconciliation > span:last-child', 'Sin datos agregados');
  const chart = $('#chart'); chart.classList.add('chart-unavailable'); chart.setAttribute('role', 'status'); chart.textContent = 'No hay una serie histórica disponible para esta consulta.';
  text('#chart-note', 'Las cifras de la operación actual no permiten reconstruir una comparación de períodos.'); text('#chart-subtitle', 'Millones de COP · serie histórica');
  $$('[data-chart]').forEach(button => button.onclick = () => {
    $$('[data-chart]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    chart.textContent = button.dataset.chart === 'sales' ? 'No hay una serie histórica de ventas disponible.' : 'No hay una serie histórica de pagos a prestamistas disponible.';
  });
  const emptyRow = el('tr'), emptyCell = el('td', '', 'Información no disponible.'); emptyCell.colSpan = 3; emptyRow.append(emptyCell); $('#chart-data').append(emptyRow);
  text('#cartera .section-heading p', 'Estado actual de los créditos'); text('.portfolio-total strong', '—');
  const bar = $('.portfolio-bar'); bar.classList.add('data-unavailable'); bar.setAttribute('aria-label', 'La distribución por capital pendiente no está disponible');
  text('.portfolio-legend > span:first-child strong', active.length - late); text('.portfolio-legend > span:last-child strong', late);
  text('.risk-metric strong', '—'); text('.risk-metric p', 'No hay datos suficientes para calcular PAR30.');
  text('.paid-line > span', 'Préstamos pagados en el histórico'); text('#paid-count', loans.filter(loan => loan.status === 'paid').length);
  for (const label of ['Propuestas iniciadas', 'Con contraparte compatible', 'Aceptadas por ambas partes', 'Con desembolso confirmado']) {
    const item = el('li'), track = el('div', 'funnel-track'); track.setAttribute('aria-hidden', 'true');
    item.append(el('strong', '', '—'), el('span', '', label), track); $('#funnel').append(item);
  }
  text('#conversion', '—'); text('#conversion-caption', 'No está disponible el seguimiento de una misma cohorte de propuestas.');
  $$('.market-footnote strong').forEach(node => node.textContent = '—'); text('.market-footnote p', 'Inventario abierto no disponible');
  for (const selector of ['#business-count', '#lender-count', '#first-credit']) text(selector, '—');
  text('.ecosystem > div:first-child p', 'Participación agregada no disponible');
  text('#atencion .section-heading p', 'Pagos retenidos o con entrega fallida en la consulta actual.');
  text('#attention-title .count', data.retained_payouts.length);
  text('.attention-table caption', 'Pagos que requieren seguimiento');
  const tbody = $('#atencion tbody'); tbody.replaceChildren();
  for (const payout of data.retained_payouts) {
    const row = el('tr'), heading = el('th'), signal = el('span', 'signal'); heading.scope = 'row';
    signal.append(el('i', 'dot amber'), document.createTextNode(deliveryState(payout.status))); heading.append(signal, el('small', '', nameFor(payout.merchant)));
    const action = el('td'), button = el('button', 'text-button', 'Ver pago'); button.dataset.payout = payout.id; action.append(button);
    row.append(heading, el('td', '', '1 pago'), el('td', '', money(payout.amount, true)), action); tbody.append(row);
  }
  if (!data.retained_payouts.length) { const row = el('tr'), cell = el('td', '', 'No hay pagos retenidos ni fallidos.'); cell.colSpan = 4; row.append(cell); tbody.append(row); }
  const dialog = $('#detail-dialog'); $('#close-detail').onclick = $('#back-detail').onclick = () => dialog.close();
  const explanations = {
    loans: ['Préstamos desembolsados', 'Créditos activos o pagados que tienen fecha de desembolso confirmada. Se cuenta todo el histórico devuelto por Trust.'],
    funded: ['Capital que llega al negocio', 'Suma del capital de créditos con desembolso confirmado. Excluye ofertas y acuerdos pendientes, ventas y cargos del crédito.'],
    active: ['Préstamos activos', 'El estado activo confirma el desembolso. El indicador de retraso viene del servidor, no se infiere por falta de ventas.'],
    rate: ['Tasa promedio ponderada', 'La información disponible expresa un cargo fijo sobre el capital. No contiene una tasa efectiva anual ni el capital pendiente necesario para esta métrica.'],
    flow: ['Una venta, dos destinos', 'El agregado de ventas y pagos a negocios y prestamistas no está disponible. Los ingresos de Trust son un concepto diferente al capital y a las ventas.'],
    portfolio: ['Salud de la cartera', 'Los contadores reflejan créditos activos al día o en retraso. El capital pendiente y PAR30 requieren datos que no se incluyen en esta consulta.'],
    matching: ['Seguir la misma propuesta', 'La conversión necesita observar una misma cohorte desde publicación hasta desembolso. Los totales por estado no permiten calcular esa conversión.'],
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-detail], [data-payout]'); if (!button) return;
    const content = $('#detail-content'); content.replaceChildren();
    if (button.dataset.payout) {
      const payout = data.retained_payouts.find(item => item.id === button.dataset.payout);
      text('#detail-title', 'Detalle del pago'); content.append(el('p', 'detail-amount', money(payout.amount, true)), el('p', '', `Comercio: ${nameFor(payout.merchant)}. Estado: ${deliveryState(payout.status)}.`), el('p', '', `Entregado: ${money(payout.sent_total, true)}. Referencia: ${payout.id}.`));
      if (payout.failure_code) content.append(el('p', '', `Motivo registrado: ${payout.failure_code}`));
    } else {
      const explanation = explanations[button.dataset.detail]; if (!explanation) return;
      text('#detail-title', explanation[0]); content.append(el('p', '', explanation[1]));
      if (button.dataset.detail === 'flow') {
        content.append(el('h3', '', 'Operación de Trust'));
        for (const [label, amounts] of finance) content.append(el('p', '', `${label}: ${amounts.map(value => money(value, true)).join(' · ')}`));
      }
    }
    dialog.showModal();
  });
  $('#export').onclick = () => downloadCSV('trust-resumen.csv', [['Métrica', 'Valor', 'Alcance'], ['Préstamos desembolsados', funded.length, 'Histórico disponible'], ['Capital desembolsado', money(capital, true), 'Histórico disponible'], ['Préstamos activos', active.length, 'Estado actual'], ...finance.flatMap(([label, values]) => values.map(value => [label, money(value, true), 'Estado actual']))]);
}
const nameFor = merchant => merchant.business_name;
