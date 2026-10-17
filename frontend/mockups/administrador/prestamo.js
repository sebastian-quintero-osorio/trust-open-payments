(() => {
  const D = window.TrustAdminLoans;
  const $ = selector => document.querySelector(selector);
  const params = new URLSearchParams(location.search);
  const backQuery = D.queryString(D.filters(params));
  $('#back-loans').href = 'prestamos.html' + (backQuery ? '?' + backQuery : '');
  const record = D.records.find(item => item.id === params.get('id'));
  if (!record) {
    $('#loan-not-found').hidden = false;
    document.title = 'Trust — Préstamo no encontrado';
    return;
  }
  document.title = `Trust — ${record.id} · Detalle del préstamo`;
  const realized = record.type === 'realizado';
  const total = D.totals(record);
  const origin = record.origin || record.type;
  const progress = Math.round(total.principal / record.amount * 100);
  const termLabel = realized || record.state === 'aceptado' ? 'acordadas' : 'propuestas';
  const info = {
    abierta: ['Buscando contraparte', origin === 'ofrecido' ? 'La oferta está disponible para negocios compatibles con sus condiciones y cupo.' : 'El negocio está buscando un prestamista compatible con su solicitud.'],
    pendiente: ['Propuesta en espera', `${D.name(record[record.waiting])} debe responder. Aún no hay aceptación de ambas partes ni desembolso.`],
    aceptado: ['Acuerdo aceptado · desembolso pendiente', 'Ambas partes aceptaron las condiciones. El registro pasará a Realizado cuando se confirme la transferencia de capital.'],
    rechazada: ['Propuesta rechazada', record.reason],
    al_dia: ['El crédito se paga con las ventas', `El ${record.share} % de cada venta recaudada se destina al préstamo, según el acuerdo. No hay obligaciones contractuales vencidas al corte.`],
    retraso: ['Requiere seguimiento', `Obligación contractual de ${D.money(record.overdueAmount)} vencida el ${record.due ? D.date(record.due) : ''}. Lleva ${record.overdue} días de retraso al corte; este importe no es todo el capital pendiente.`],
    pagado: ['Crédito pagado', `El préstamo terminó de pagarse el ${record.paid ? D.date(record.paid) : ''}. Ya no tiene capital pendiente ni recaudos pendientes en esta demostración.`],
  }[record.state];

  function participant(role, key) {
    const party = D.parties[key];
    if (!party) return `<div class="loan-party"><div class="party-heading"><span class="party-avatar" aria-hidden="true">—</span><div><span>${role}</span><h3>Sin contraparte</h3></div></div><p>${role === 'Prestamista' ? 'Pendiente de recibir una oferta compatible.' : 'Pendiente de encontrar un negocio compatible.'}</p></div>`;
    const initials = party.name.split(' ').slice(0, 2).map(word => word[0]).join('');
    return `<div class="loan-party"><div class="party-heading"><span class="party-avatar" aria-hidden="true">${initials}</span><div><span>${role}</span><h3>${party.name}</h3></div></div><dl class="party-facts"><div><dt>Referencia</dt><dd>${party.reference}</dd></div><div><dt>Ubicación</dt><dd>${party.city}, Colombia</dd></div>${party.sector ? `<div><dt>Actividad</dt><dd>${party.sector}</dd></div>` : ''}${party.credit && !realized ? `<div><dt>Cupo al publicar</dt><dd>${D.money(party.credit)} COP</dd></div>` : ''}</dl></div>`;
  }

  function timeline() {
    const events = [{ date: record.created, title: origin === 'ofrecido' ? 'Oferta publicada' : 'Solicitud publicada', text: `${D.name(origin === 'ofrecido' ? record.lender : record.borrower)} publicó las condiciones iniciales.` }];
    if (record.proposed) events.push({ date: record.proposed, title: 'Propuesta enviada', text: 'La contraparte manifestó su aceptación de las condiciones.' });
    if (record.rejected) events.push({ date: record.rejected, title: 'Propuesta rechazada', text: record.reason });
    if (record.accepted) events.push({ date: record.accepted, title: 'Acuerdo aceptado por ambas partes', text: 'Prestamista y prestatario aceptaron las mismas condiciones.' });
    if (record.funded) events.push({ date: record.funded, title: 'Desembolso confirmado', text: `${D.money(record.amount)} COP transferidos al negocio. Referencia DES-${record.id}.` });
    if (record.due) events.push({ date: record.due, title: 'Obligación contractual vencida', text: `${D.money(record.overdueAmount)} COP pendientes de esa obligación al corte.` });
    if (record.paid) events.push({ date: record.paid, title: 'Préstamo pagado', text: 'Capital recuperado en su totalidad y crédito cerrado.' });
    else if (record.payments?.length) events.push({ date: record.payments.at(-1).date, title: 'Último recaudo confirmado', text: `${D.money(record.payments.at(-1).principal + record.payments.at(-1).interest)} COP abonados al crédito.` });
    events.sort((a, b) => a.date.localeCompare(b.date));
    return `<ol class="loan-timeline">${events.map(event => `<li><time datetime="${event.date}">${D.date(event.date)}</time><div><h3>${event.title}</h3><p>${event.text}</p></div></li>`).join('')}</ol>`;
  }

  function repayment() {
    if (!realized) return `<section class="panel unfunded-panel" aria-labelledby="repayment-title"><svg class="icon" aria-hidden="true"><use href="#i-flow"/></svg><h2 id="repayment-title">Aún no hay movimientos de este préstamo</h2><p>Sin desembolso confirmado, no hay capital por recuperar ni pagos del crédito. ${record.state === 'aceptado' ? 'El acuerdo ya está aceptado y espera la transferencia.' : 'Las condiciones no representan un crédito desembolsado.'}</p></section>`;
    const lastPayment = record.payments.at(-1);
    return `<section class="panel repayment-panel" aria-labelledby="repayment-title"><div class="section-heading"><div><h2 id="repayment-title">Pago del crédito</h2><p>Acumulado desde el desembolso hasta el 30 sep 2026</p></div><span class="loan-badge ${D.states[record.state].tone}">${progress} % del capital recuperado</span></div><div class="repayment-figures"><div><span>Capital desembolsado</span><strong>${D.money(record.amount)}</strong></div><div><span>Capital recuperado</span><strong>${D.money(total.principal)}</strong></div><div><span>Capital pendiente</span><strong>${D.money(total.pending)}</strong></div></div><progress class="repayment-progress" value="${total.principal}" max="${record.amount}" aria-label="Capital recuperado">${progress} %</progress><div class="repayment-foot"><span>Intereses cobrados <strong>${D.money(total.interest)} COP</strong></span><span>Último recaudo: ${D.date(lastPayment.date)}</span></div></section>
      <section class="panel distribution-panel" aria-labelledby="distribution-title"><div class="section-heading"><div><h2 id="distribution-title">Así se distribuyeron las ventas</h2><p>Recaudos confirmados vía Bre-B / Puntored vinculados a este crédito</p></div></div><div class="loan-distribution"><div class="sales-origin"><span>Ventas recaudadas</span><strong>${D.money(total.sales)}</strong><small>COP · Desde el desembolso</small></div><div class="distribution-destinations"><div><span>Para el negocio <small>${100 - record.share} % de las ventas</small></span><strong>${D.money(total.business)}</strong></div><div><span>Para el prestamista <small>${record.share} % de las ventas</small></span><strong>${D.money(total.collected)}</strong></div></div></div><p class="distribution-note">${D.money(total.collected)} al prestamista = ${D.money(total.principal)} de capital + ${D.money(total.interest)} de intereses. Sin comisiones ni devoluciones modeladas.</p></section>
      <section class="panel payments-panel" aria-labelledby="payments-title"><div class="section-heading"><div><h2 id="payments-title">Recaudos del préstamo</h2><p>Todos los recaudos de este ejemplo · COP</p></div><span class="scope-marker">${record.payments.length} confirmados</span></div><div class="table-scroll" tabindex="0" role="region" aria-label="Recaudos confirmados, tabla desplazable"><table class="payments-table"><caption class="sr-only">Desglose de cada recaudo confirmado en capital e intereses</caption><thead><tr><th scope="col">Fecha / referencia</th><th scope="col">Capital</th><th scope="col">Intereses</th><th scope="col">Total al crédito</th></tr></thead><tbody>${[...record.payments].reverse().map((payment, index) => `<tr><th scope="row">${D.date(payment.date)}<small>REC-${record.id}-${String(record.payments.length - index).padStart(2, '0')}</small></th><td>${D.money(payment.principal)}</td><td>${D.money(payment.interest)}</td><td>${D.money(payment.principal + payment.interest)}</td></tr>`).join('')}</tbody><tfoot><tr><th scope="row">Total confirmado</th><td>${D.money(total.principal)}</td><td>${D.money(total.interest)}</td><td>${D.money(total.collected)}</td></tr></tfoot></table></div></section>`;
  }

  $('#loan-detail').innerHTML = `
    <div class="page-heading detail-heading"><div><div class="detail-reference"><span>${record.id}</span><span class="scope-marker">Solo consulta</span></div><h1>Detalle del préstamo</h1><p>${origin === 'ofrecido' ? 'Originado en una oferta de financiación' : 'Originado en una solicitud de crédito'} · Creado el ${D.date(record.created)}</p></div><div class="detail-stage"><span class="loan-type">${D.types[record.type]}</span>${D.badge(record)}</div></div>
    <section class="loan-status-note ${D.states[record.state].tone}" aria-label="Situación del préstamo"><svg class="icon" aria-hidden="true"><use href="#i-info"/></svg><div><h2>${info[0]}</h2><p>${info[1]}</p></div></section>
    <section class="loan-agreement" aria-labelledby="agreement-title"><div class="agreement-amount"><h2 id="agreement-title">${realized ? 'Capital desembolsado' : record.min !== record.amount ? 'Rango de monto propuesto' : record.state === 'aceptado' ? 'Monto acordado' : 'Monto propuesto'}</h2><strong>${D.amount(record)}</strong><span>COP</span><p>${record.purpose}</p></div><div class="agreement-terms"><h2>Condiciones ${termLabel}</h2><dl><div><dt>${origin === 'solicitado' && !realized && record.state !== 'aceptado' ? 'Tasa máxima propuesta' : 'Tasa de interés'}</dt><dd>${D.number(record.rate)} <small>% E.A.</small></dd></div><div><dt>Plazo</dt><dd>${record.term} <small>meses</small></dd></div><div><dt>Aporte de las ventas</dt><dd>${record.share} <small>%</small></dd></div></dl><p>La tasa de interés y el porcentaje de ventas son conceptos distintos. El aporte de cada venta paga capital e intereses.</p></div></section>
    <section class="panel participants-panel" aria-labelledby="participants-title"><div class="section-heading"><div><h2 id="participants-title">Las partes del préstamo</h2><p>${record.accepted ? 'Ambas partes aceptaron el ' + D.date(record.accepted) : 'Participantes registrados al corte'}</p></div></div><div class="loan-party-grid">${participant('Prestatario', record.borrower)}${participant('Prestamista', record.lender)}</div></section>
    ${repayment()}
    <section class="panel timeline-panel" aria-labelledby="timeline-title"><div class="section-heading"><div><h2 id="timeline-title">Recorrido del préstamo</h2><p>Hitos registrados en esta demostración</p></div></div>${timeline()}</section>
    <p class="register-note">Información de demostración al 30 sep 2026. Las referencias de acuerdos, desembolsos y recaudos son ficticias.</p>`;
})();
