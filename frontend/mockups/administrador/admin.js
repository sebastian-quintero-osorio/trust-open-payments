(() => {
  'use strict';
  // Fixture independiente. Importes expresados en millones de COP, nunca en moneda mezclada.
  // Ambos períodos terminan en el mismo corte: los saldos no se filtran como movimientos.
  const periods = {
    month: {
      label: 'Septiembre 2026', previous: 'agosto 2026', start: '2026-09-01', end: '2026-09-30',
      previousStart: '2026-08-01', previousEnd: '2026-08-31',
      newLoans: 186, previousLoans: 162, funded: 620, previousFunded: 540, paid: 110,
      sales: 864, previousSales: 768, businesses: 752, lenders: 112,
      principal: 92, interest: 20,
      activeBusinesses: 986, activeLenders: 214, firstCredit: 72,
      funnel: [320, 240, 204, 186],
      labels: ['1–7', '8–14', '15–21', '22–30'], previousLabels: ['1–7 ago', '8–14 ago', '15–21 ago', '22–31 ago'],
      chartSales: [168, 192, 216, 288], previousChartSales: [150, 174, 204, 240],
      chartLenders: [21.6, 24.8, 28.8, 36.8], previousChartLenders: [19.2, 22.4, 25.6, 28.8],
      chartNote: 'Tramos de septiembre vs. agosto. El último tramo tiene 9 y 10 días, respectivamente; son totales, no promedios diarios.',
    },
    week: {
      label: '24–30 septiembre 2026', previous: '17–23 septiembre 2026', start: '2026-09-24', end: '2026-09-30',
      previousStart: '2026-09-17', previousEnd: '2026-09-23',
      newLoans: 52, previousLoans: 46, funded: 180, previousFunded: 152, paid: 26,
      sales: 224, previousSales: 196, businesses: 194.8, lenders: 29.2,
      principal: 24, interest: 5.2,
      activeBusinesses: 742, activeLenders: 182, firstCredit: 18,
      funnel: [92, 68, 57, 52],
      labels: ['24', '25', '26', '27', '28', '29', '30'], previousLabels: ['17 sep', '18 sep', '19 sep', '20 sep', '21 sep', '22 sep', '23 sep'],
      chartSales: [28, 32, 30, 34, 35, 31, 34], previousChartSales: [25, 26, 27, 30, 29, 31, 28],
      chartLenders: [3.7, 4.1, 3.9, 4.4, 4.5, 4.1, 4.5], previousChartLenders: [3.2, 3.4, 3.5, 3.9, 3.8, 4, 3.8],
      chartNote: 'Cada día del 24–30 de septiembre se compara con el mismo día de la semana anterior, del 17–23.',
    },
  };
  const snapshot = { active: 1284, principal: 3840, onTime: 1208, late: 76, latePrincipal: 230.4, par30Loans: 51, par30Principal: 184.32, rate: 18.4 };
  let period = 'month';
  let chartMetric = 'sales';
  const $ = selector => document.querySelector(selector);
  const number = (value, digits = 0) => new Intl.NumberFormat('es-CO', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
  const amount = value => `$${number(value, Number.isInteger(value) ? 0 : 1)}`;
  const percentage = value => `${number(value, 1)} %`;
  const delta = (current, previous) => `${current >= previous ? '+' : '−'}${percentage(Math.abs((current / previous - 1) * 100))}`;
  const sum = values => values.reduce((total, value) => total + value, 0);

  function renderChart() {
    const data = periods[period];
    const values = chartMetric === 'sales' ? data.chartSales : data.chartLenders;
    const previous = chartMetric === 'sales' ? data.previousChartSales : data.previousChartLenders;
    const name = chartMetric === 'sales' ? 'Ventas recaudadas' : 'Pagos confirmados a prestamistas';
    const maximum = Math.ceil(Math.max(...values, ...previous) / 4) * 4;
    const width = 540, height = 230, left = 40, top = 26, bottom = 190;
    const groupWidth = (width - left - 10) / values.length;
    const barWidth = Math.min(26, groupWidth * 0.29);
    const y = value => bottom - (value / maximum) * (bottom - top);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="chart-title chart-description"><title id="chart-title">${name}: ${data.label}</title><desc id="chart-description">${amount(sum(values))} millones de COP, frente a ${amount(sum(previous))} millones en ${data.previous}. Valores exactos disponibles en Ver datos del gráfico.</desc>`;
    for (let tick = 0; tick <= 4; tick++) {
      const value = maximum * tick / 4;
      svg += `<line x1="${left}" x2="${width}" y1="${y(value)}" y2="${y(value)}" stroke="#39313f" stroke-dasharray="3 5"/><text x="${left - 9}" y="${y(value) + 4}" text-anchor="end">${number(value)}</text>`;
    }
    values.forEach((value, index) => {
      const center = left + groupWidth * (index + 0.5);
      svg += `<g><title>${data.labels[index]} sep: ${amount(value)} M; ${data.previousLabels[index]}: ${amount(previous[index])} M</title><rect x="${center - barWidth - 2}" y="${y(previous[index])}" width="${barWidth}" height="${bottom - y(previous[index])}" rx="3" fill="#685b78"/><rect x="${center + 2}" y="${y(value)}" width="${barWidth}" height="${bottom - y(value)}" rx="3" fill="#a379f3"/><text class="value-label" x="${center + 2 + barWidth / 2}" y="${y(value) - 8}" text-anchor="middle">${number(value, value % 1 ? 1 : 0)}</text><text x="${center}" y="${bottom + 23}" text-anchor="middle">${data.labels[index]}</text></g>`;
    });
    $('#chart').innerHTML = `${svg}</svg>`;
    $('#chart-note').textContent = data.chartNote;
    $('#chart-subtitle').textContent = `Millones de COP · ${period === 'month' ? 'acumulados por tramo' : 'totales diarios'}`;
    $('#chart-data').innerHTML = values.map((value, index) => `<tr><th scope="row">${data.labels[index]} sep / ${data.previousLabels[index]}</th><td>${amount(value)} M</td><td>${amount(previous[index])} M</td></tr>`).join('');
    document.querySelectorAll('[data-chart]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.chart === chartMetric)));
  }

  function render() {
    const data = periods[period];
    $('#comparison-label').textContent = `Comparado con ${data.previous}`;
    const cards = [
      { label: 'Préstamos desembolsados', value: number(data.newLoans), context: 'Nuevos créditos en el período', change: `${delta(data.newLoans, data.previousLoans)} vs. período anterior`, detail: 'loans' },
      { label: 'Capital desembolsado', value: `${amount(data.funded)} <small>M COP</small>`, context: 'Financiación entregada a negocios', change: `${delta(data.funded, data.previousFunded)} vs. período anterior`, detail: 'funded' },
      { label: 'Préstamos activos', value: number(snapshot.active), context: 'Con capital pendiente al corte', change: '1.208 al día · 76 en retraso', detail: 'active', neutral: true },
      { label: 'Tasa promedio ponderada', value: `${number(snapshot.rate, 1)} <small>% E.A.</small>`, context: 'Ponderada por capital pendiente', change: 'Cartera activa al 30 sep 2026', detail: 'rate', neutral: true },
    ];
    $('#kpis').innerHTML = cards.map(card => `<article class="kpi"><div class="kpi-label"><span>${card.label}</span><button type="button" class="info-button" data-detail="${card.detail}" aria-label="Definición: ${card.label}">i</button></div><strong class="kpi-value">${card.value}</strong><span class="kpi-context">${card.context}</span><span class="change${card.neutral ? ' neutral' : ''}">${card.change}</span></article>`).join('');
    $('#sales-total').textContent = amount(data.sales);
    $('#sales-change').textContent = `${delta(data.sales, data.previousSales)} vs. ${data.previous}`;
    const destinations = [
      { label: 'Pagado a negocios', value: data.businesses, note: 'Abonos por ventas, después del aporte al crédito', className: '' },
      { label: 'Pagado a prestamistas', value: data.lenders, note: `${amount(data.principal)} M capital + ${amount(data.interest)} M intereses`, className: '' },
    ];
    $('#destinations').innerHTML = destinations.map(item => `<div class="destination ${item.className}"><span class="destination-label">${item.label}</span><strong>${amount(item.value)} <small>M</small></strong><p>${item.note}</p><span class="destination-share">${percentage(item.value / data.sales * 100)}</span></div>`).join('');
    $('#reconciliation').textContent = `${amount(data.sales)} M = ${amount(data.businesses)} M a negocios + ${amount(data.lenders)} M a prestamistas`;
    $('#paid-count').textContent = number(data.paid);
    const labels = ['Propuestas iniciadas', 'Con contraparte compatible', 'Aceptadas por ambas partes', 'Con desembolso confirmado'];
    $('#funnel').innerHTML = data.funnel.map((value, index) => `<li><strong>${number(value)}</strong><span>${labels[index]}</span><div class="funnel-track" aria-hidden="true"><i style="width:${value / data.funnel[0] * 100}%"></i></div></li>`).join('');
    $('#conversion').textContent = percentage(data.funnel[3] / data.funnel[0] * 100);
    $('#conversion-caption').textContent = `${data.funnel[3]} de ${data.funnel[0]} propuestas del período llegaron al desembolso.`;
    $('#business-count').textContent = number(data.activeBusinesses);
    $('#lender-count').textContent = number(data.activeLenders);
    $('#first-credit').textContent = number(data.firstCredit);
    renderChart();
  }

  function showDetail(key) {
    const data = periods[period];
    const details = {
      loans: ['Préstamos desembolsados', `<p class="detail-amount">${data.newLoans} nuevos créditos</p><p>Préstamos únicos cuyo desembolso fue confirmado entre ${data.start} y ${data.end}. Firmar un acuerdo no basta para contar un desembolso.</p><p>Período anterior (${data.previous}): ${data.previousLoans}. En este ejemplo todos los desembolsos pertenecen a propuestas iniciadas en el mismo período. En producción, la cohorte de matching puede diferir del total desembolsado.</p>`],
      funded: ['Capital que llega al negocio', `<p class="detail-amount">${amount(data.funded)} millones de COP</p><p>Suma del capital efectivamente desembolsado en ${data.label}. Excluye ofertas publicadas, acuerdos pendientes de desembolso e intereses futuros.</p><p>Este monto es financiación; no se incluye en las ventas recaudadas ni en los abonos por ventas a negocios. Período anterior: ${amount(data.previousFunded)} M.</p>`],
      active: ['Préstamos con saldo de capital', '<p class="detail-amount">1.284 préstamos activos</p><p>Foto al 30 sep 2026: 1.208 al día y 76 en retraso. Excluye solicitudes, ofertas sin firma, acuerdos sin desembolso y créditos ya pagados.</p><p>La cantidad de préstamos no equivale a negocios únicos: un negocio puede tener más de un crédito. Este saldo no cambia al alternar períodos con la misma fecha de corte.</p>'],
      rate: ['El costo pactado de la cartera', '<p class="detail-amount">18,4 % efectiva anual</p><p>Σ (tasa E.A. pactada × capital pendiente del préstamo) / Σ capital pendiente. Se incluyen exclusivamente préstamos activos en COP al corte.</p><p>La ponderación refleja el costo asociado al capital aún expuesto. No es rentabilidad efectiva ni tasa de aporte de las ventas. El historial del prestamista conserva su promedio simple de todos los préstamos firmados; responde a otra pregunta.</p>'],
      flow: ['Una venta, dos destinos', `<p class="detail-amount">${amount(data.sales)} M recaudados</p><p>De las ventas confirmadas en ${data.label}, ${amount(data.businesses)} M ya se abonaron a los negocios y ${amount(data.lenders)} M a los prestamistas.</p><p>Los pagos a prestamistas contienen ${amount(data.principal)} M de capital recuperado y ${amount(data.interest)} M de intereses cobrados. Los intereses pertenecen a los prestamistas: no son ingresos de Trust.</p><p>Los porcentajes son la distribución agregada observada, no el porcentaje contractual de cada préstamo. Se excluyen ventas fallidas y desembolsos; en esta simulación todas las ventas están distribuidas, sin devoluciones ni comisiones.</p>`],
      portfolio: ['Qué capital sigue expuesto', '<p class="detail-amount">$3.840 M de capital pendiente</p><p>$3.609,6 M corresponden a los 1.208 préstamos al día (94 % del capital). $230,4 M corresponden a los 76 préstamos en retraso (6 %). La barra representa dinero; los contadores representan préstamos.</p><p>Dentro de los préstamos en retraso, 51 superan los 30 días y concentran $184,32 M de capital pendiente. PAR30 = $184,32 M / $3.840 M = 4,8 %.</p><p>PAR30 mide todo el capital pendiente de esos préstamos, no únicamente una cuota vencida. La fecha exigible debe salir del acuerdo; si no existe obligación vencida, una baja de ventas es una señal de actividad, no mora.</p>'],
      matching: ['Seguir la misma propuesta', `<p class="detail-amount">${percentage(data.funnel[3] / data.funnel[0] * 100)} llegó al desembolso</p><p>De las ${data.funnel[0]} propuestas únicas iniciadas en ${data.label}, ${data.funnel[1]} encontraron al menos una contraparte compatible, ${data.funnel[2]} fueron aceptadas por ambas partes y ${data.funnel[3]} tuvieron un desembolso confirmado antes del corte.</p><p>Compatibilidad propuesta: monto dentro de los rangos de ambas partes y del cupo disponible; tasa, plazo y porcentaje de ventas compatibles. Un match no implica aceptación ni garantía de financiación.</p><p>Los pasos son acumulativos y anidados; una propuesta se cuenta una sola vez por paso. La cohorte más reciente ha tenido menos tiempo para convertirse. Las 132 solicitudes y 84 ofertas abiertas son inventario al corte y no se suman al embudo.</p>`],
      inactive: ['Señal temprana de actividad', '<p class="detail-amount">48 préstamos · $126 M</p><p>Préstamos activos de negocios sin ventas confirmadas en Trust entre el 24 y el 30 de septiembre. El monto representa su capital pendiente.</p><p><strong>Siguiente paso:</strong> revisar si el negocio sigue operando, si está cobrando fuera del canal o si existe un problema de recaudo. Comparar con su patrón habitual y asignar seguimiento.</p><p>El umbral de siete días es una propuesta y deberá considerar estacionalidad. La ausencia de ventas no demuestra incumplimiento contractual. Puede haber préstamos que también estén en la cola de retrasos.</p>'],
      risk: ['Capital con retraso contractual', '<p class="detail-amount">51 préstamos · $184,32 M</p><p>Préstamos con una obligación exigible impagada por más de 30 días al corte. Constituyen el 4,8 % del capital pendiente total.</p><p><strong>Siguiente paso:</strong> validar el vencimiento y los abonos conciliados, revisar la evolución de ventas y asignar seguimiento de recuperación según el acuerdo.</p><p>Esta clasificación requiere reglas contractuales verificables. El mockup muestra un escenario ilustrativo; no infiere retraso solo porque el negocio dejó de vender.</p>'],
    };
    if (!details[key]) return;
    $('#detail-title').textContent = details[key][0];
    $('#detail-content').innerHTML = details[key][1];
    $('#detail-dialog').showModal();
  }

  $('#period').addEventListener('change', event => {
    period = event.target.value;
    render();
    $('#filter-status').textContent = `Mostrando ${periods[period].label}. Comparación con ${periods[period].previous}. Los saldos y alertas conservan el corte del 30 de septiembre.`;
  });
  document.addEventListener('click', event => {
    const detail = event.target.closest('[data-detail]');
    if (detail) showDetail(detail.dataset.detail);
    const chart = event.target.closest('[data-chart]');
    if (chart) { chartMetric = chart.dataset.chart; renderChart(); }
  });
  $('#close-detail').addEventListener('click', () => $('#detail-dialog').close());
  $('#back-detail').addEventListener('click', () => $('#detail-dialog').close());

  $('#export').addEventListener('click', () => {
    const data = periods[period];
    const rows = [['Indicador', 'Valor', 'Unidad', 'Tipo', 'Inicio', 'Fin o corte', 'Criterio']];
    const add = (name, value, unit, kind, criterion) => rows.push([name, value, unit, kind, kind === 'Movimiento' ? data.start : '', '2026-09-30', criterion]);
    const flowRows = [
      ['Préstamos desembolsados', data.newLoans, 'préstamos', 'Desembolso confirmado en el período'],
      ['Capital desembolsado', data.funded * 1e6, 'COP', 'Capital nuevo; excluido de ventas'],
      ['Ventas recaudadas', data.sales * 1e6, 'COP', 'Ventas confirmadas del período'],
      ['Pagado a negocios', data.businesses * 1e6, 'COP', 'Abonos confirmados por ventas del período'],
      ['Pagado a prestamistas', data.lenders * 1e6, 'COP', 'Pagos confirmados de las ventas del período'],
      ['Capital recuperado', data.principal * 1e6, 'COP', 'Componente de pagos a prestamistas'],
      ['Intereses cobrados por prestamistas', data.interest * 1e6, 'COP', 'Componente de pagos a prestamistas; no ingreso de Trust'],
      ['Préstamos pagados', data.paid, 'préstamos', 'Créditos que terminaron de pagarse en el período'],
      ['Negocios con ventas', data.activeBusinesses, 'negocios únicos', 'Con una o más ventas confirmadas'],
      ['Prestamistas con pagos', data.activeLenders, 'prestamistas únicos', 'Con uno o más pagos confirmados'],
      ['Negocios con primer crédito en Trust', data.firstCredit, 'negocios únicos', 'Primer desembolso histórico dentro de Trust'],
    ];
    flowRows.forEach(row => add(row[0], row[1], row[2], 'Movimiento', row[3]));
    add('Préstamos activos', snapshot.active, 'préstamos', 'Saldo al corte', 'Con capital pendiente');
    add('Tasa promedio ponderada E.A.', snapshot.rate, '% E.A.', 'Saldo al corte', 'Σ(tasa E.A. × capital pendiente) / capital pendiente total');
    add('Capital pendiente', snapshot.principal * 1e6, 'COP', 'Saldo al corte', 'Excluye intereses futuros');
    add('Préstamos al día', snapshot.onTime, 'préstamos', 'Saldo al corte', 'Sin retraso contractual');
    add('Préstamos en retraso', snapshot.late, 'préstamos', 'Saldo al corte', 'Obligación contractual vencida');
    add('Capital en préstamos en retraso', snapshot.latePrincipal * 1e6, 'COP', 'Saldo al corte', 'Capital completo expuesto');
    add('Capital en préstamos con retraso mayor a 30 días', snapshot.par30Principal * 1e6, 'COP', 'Saldo al corte', 'Capital completo expuesto, no solo cuota vencida');
    add('PAR30', snapshot.par30Principal / snapshot.principal * 100, '%', 'Saldo al corte', 'Capital pendiente con retraso >30 días / capital pendiente total');
    add('Solicitudes abiertas', 132, 'solicitudes', 'Saldo al corte', 'Inventario sin acuerdo firmado');
    add('Ofertas abiertas', 84, 'ofertas', 'Saldo al corte', 'Inventario sin acuerdo firmado');
    ['Propuestas iniciadas', 'Con contraparte compatible', 'Aceptadas por ambas partes', 'Con desembolso confirmado'].forEach((name, i) => add(name, data.funnel[i], 'propuestas únicas', 'Movimiento', 'Cohorte iniciada en el período, observada al corte'));
    add('Conversión a desembolso', Number((data.funnel[3] / data.funnel[0] * 100).toFixed(4)), '%', 'Movimiento', 'Desembolsadas / propuestas iniciadas de la misma cohorte');
    add('Préstamos sin ventas en 7 días', 48, 'préstamos', 'Saldo al corte', '126000000 COP expuestos; no implica mora');
    add('Préstamos con retraso >30 días', snapshot.par30Loans, 'préstamos', 'Saldo al corte', '184320000 COP expuestos');
    rows.push(['Comparación anterior', data.previous, '', 'Metadato', data.previousStart, data.previousEnd, 'Comparación de movimientos']);
    rows.push(['Préstamos desembolsados anteriores', data.previousLoans, 'préstamos', 'Comparación', data.previousStart, data.previousEnd, '']);
    rows.push(['Capital desembolsado anterior', data.previousFunded * 1e6, 'COP', 'Comparación', data.previousStart, data.previousEnd, '']);
    rows.push(['Ventas recaudadas anteriores', data.previousSales * 1e6, 'COP', 'Comparación', data.previousStart, data.previousEnd, '']);
    rows.push(['Fuente', 'Datos ficticios', '', 'Metadato', '', '', 'Mockup 08. Ventas totalmente distribuidas, sin comisiones ni devoluciones. Corte 23:59 COT.']);
    const csv = '\uFEFF' + rows.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(';')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `trust-resumen-${data.start}-${data.end}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $('#preview-message').textContent = `Resumen de ${data.label} exportado en CSV. Los importes están en COP completos y se identifican como datos ficticios.`;
    $('#preview-notice').hidden = false;
  });
  render();
})();
