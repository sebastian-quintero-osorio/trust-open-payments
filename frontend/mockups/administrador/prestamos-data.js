/* Muestra administrativa independiente. No lee cuentas locales ni ejecuta pagos. */
(() => {
  const parties = {
    aliado: { name: 'Aliado Capital', city: 'Bogotá', reference: 'PRE-001' },
    camila: { name: 'Camila Moreno', city: 'Medellín', reference: 'PRE-002' },
    horizonte: { name: 'Horizonte Capital', city: 'Cali', reference: 'PRE-003' },
    esquina: { name: 'La Esquina', city: 'Bogotá', reference: 'COM-001', sector: 'Comercio de alimentos', credit: 8000000 },
    taller: { name: 'Taller Central', city: 'Medellín', reference: 'COM-002', sector: 'Servicios automotrices', credit: 6000000 },
    panaderia: { name: 'Panadería Aurora', city: 'Cali', reference: 'COM-003', sector: 'Panadería', credit: 12000000 },
    semillas: { name: 'Semillas del Sur', city: 'Pasto', reference: 'COM-004', sector: 'Insumos agrícolas', credit: 15000000 },
    textil: { name: 'Textiles Luna', city: 'Barranquilla', reference: 'COM-005', sector: 'Confección', credit: 7000000 },
  };
  const types = { ofrecido: 'Ofrecido', solicitado: 'Solicitado', realizado: 'Realizado' };
  const states = {
    abierta: { label: 'Abierta', tone: 'lavender', types: ['ofrecido', 'solicitado'] },
    pendiente: { label: 'Esperando respuesta', tone: 'neutral', types: ['ofrecido', 'solicitado'] },
    aceptado: { label: 'Por desembolsar', tone: 'lavender', types: ['ofrecido', 'solicitado'] },
    rechazada: { label: 'Rechazada', tone: 'neutral', types: ['ofrecido', 'solicitado'] },
    al_dia: { label: 'Al día', tone: 'green', types: ['realizado'] },
    retraso: { label: 'En retraso', tone: 'amber', types: ['realizado'] },
    pagado: { label: 'Pagado', tone: 'green', types: ['realizado'] },
  };
  const records = [
    { id: 'OF-2609-004', type: 'ofrecido', state: 'abierta', lender: 'horizonte', borrower: null, min: 5000000, amount: 10000000, rate: 20, share: 12, term: 12, created: '2026-09-30', purpose: 'Financiación para ampliar la operación de pequeños negocios.' },
    { id: 'SO-2609-004', type: 'solicitado', state: 'pendiente', lender: 'camila', borrower: 'taller', min: 3000000, amount: 3000000, rate: 16, share: 8, term: 12, created: '2026-09-29', proposed: '2026-09-30', purpose: 'Compra de herramientas y repuestos para atender más servicios.', waiting: 'borrower' },
    { id: 'OF-2609-003', type: 'ofrecido', state: 'pendiente', lender: 'aliado', borrower: 'esquina', min: 5000000, amount: 5000000, rate: 18, share: 10, term: 12, created: '2026-09-28', proposed: '2026-09-29', purpose: 'Capital de trabajo para renovar inventario.', waiting: 'lender' },
    { id: 'SO-2609-003', type: 'solicitado', state: 'abierta', lender: null, borrower: 'semillas', min: 8000000, amount: 12000000, rate: 18, share: 15, term: 18, created: '2026-09-27', purpose: 'Inventario de semillas y fertilizantes para la próxima temporada.' },
    { id: 'OF-2609-002', type: 'ofrecido', state: 'abierta', lender: 'camila', borrower: null, min: 2000000, amount: 3000000, rate: 16, share: 8, term: 12, created: '2026-09-26', purpose: 'Financiación para fortalecer pequeños comercios.' },
    { id: 'SO-2609-002', type: 'solicitado', state: 'aceptado', lender: 'horizonte', borrower: 'panaderia', min: 8000000, amount: 8000000, rate: 19, share: 12, term: 18, created: '2026-09-25', proposed: '2026-09-26', accepted: '2026-09-28', purpose: 'Compra de un horno y adecuación del punto de venta.' },
    { id: 'OF-2609-001', type: 'ofrecido', state: 'rechazada', lender: 'aliado', borrower: 'textil', min: 4000000, amount: 4000000, rate: 18, share: 10, term: 12, created: '2026-09-24', proposed: '2026-09-25', rejected: '2026-09-26', purpose: 'Capital para comprar telas e insumos.', reason: 'La contraparte no aceptó el plazo propuesto.' },
    { id: 'SO-2609-001', type: 'solicitado', state: 'abierta', lender: null, borrower: 'textil', min: 3000000, amount: 6000000, rate: 17, share: 10, term: 18, created: '2026-09-23', purpose: 'Ampliar la capacidad de producción del taller de confección.' },
    { id: 'PR-2609-002', type: 'realizado', origin: 'ofrecido', state: 'al_dia', lender: 'aliado', borrower: 'esquina', amount: 5000000, rate: 18, share: 10, term: 12, created: '2026-09-02', proposed: '2026-09-03', accepted: '2026-09-04', funded: '2026-09-05', purpose: 'Renovación de inventario de la tienda.', payments: [
      { date: '2026-09-12', principal: 400000, interest: 20000 },
      { date: '2026-09-21', principal: 500000, interest: 25000 },
      { date: '2026-09-30', principal: 300000, interest: 15000 },
    ] },
    { id: 'PR-2609-001', type: 'realizado', origin: 'solicitado', state: 'al_dia', lender: 'camila', borrower: 'taller', amount: 3000000, rate: 16, share: 8, term: 12, created: '2026-09-01', proposed: '2026-09-02', accepted: '2026-09-03', funded: '2026-09-04', purpose: 'Compra de herramientas para el taller.', payments: [
      { date: '2026-09-13', principal: 200000, interest: 12000 },
      { date: '2026-09-22', principal: 250000, interest: 14000 },
      { date: '2026-09-29', principal: 150000, interest: 10000 },
    ] },
    { id: 'PR-2606-001', type: 'realizado', origin: 'solicitado', state: 'retraso', lender: 'horizonte', borrower: 'semillas', amount: 10000000, rate: 20, share: 15, term: 18, created: '2026-06-01', proposed: '2026-06-02', accepted: '2026-06-03', funded: '2026-06-04', due: '2026-08-25', overdue: 36, overdueAmount: 450000, purpose: 'Inventario para la temporada de siembra.', payments: [
      { date: '2026-06-30', principal: 800000, interest: 100000 },
      { date: '2026-07-31', principal: 700000, interest: 80000 },
      { date: '2026-08-20', principal: 300000, interest: 60000 },
    ] },
    { id: 'PR-2602-001', type: 'realizado', origin: 'ofrecido', state: 'pagado', lender: 'aliado', borrower: 'panaderia', amount: 6000000, rate: 18, share: 12, term: 8, created: '2026-02-01', proposed: '2026-02-02', accepted: '2026-02-03', funded: '2026-02-04', paid: '2026-09-20', purpose: 'Adecuación del local y compra de utensilios.', payments: [
      { date: '2026-04-30', principal: 2000000, interest: 100000 },
      { date: '2026-07-31', principal: 2000000, interest: 100000 },
      { date: '2026-09-20', principal: 2000000, interest: 40000 },
    ] },
  ];
  const money = value => '$' + new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 }).format(value);
  const number = value => new Intl.NumberFormat('es-CO', { maximumFractionDigits: 2 }).format(value);
  const date = value => new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(value + 'T12:00:00Z'));
  const escape = value => String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  const name = id => parties[id]?.name || 'Sin contraparte';
  const amount = record => record.min && record.min !== record.amount ? `${money(record.min)} – ${money(record.amount)}` : money(record.amount);
  const badge = record => `<span class="loan-badge ${states[record.state].tone}">${states[record.state].label}</span>`;
  function totals(record) {
    const principal = (record.payments || []).reduce((sum, payment) => sum + payment.principal, 0);
    const interest = (record.payments || []).reduce((sum, payment) => sum + payment.interest, 0);
    const collected = principal + interest;
    const sales = Math.round(collected * 100 / record.share);
    return { principal, interest, collected, sales, business: sales - collected, pending: record.amount - principal };
  }
  function filters(params) {
    const type = Object.hasOwn(types, params.get('tipo')) ? params.get('tipo') : 'todos';
    const requestedState = params.get('estado');
    const state = Object.hasOwn(states, requestedState) && (type === 'todos' || states[requestedState].types.includes(type)) ? requestedState : 'todos';
    const order = ['recientes', 'antiguos', 'monto_mayor', 'monto_menor'].includes(params.get('orden')) ? params.get('orden') : 'recientes';
    const page = Number(params.get('pagina'));
    return { type, state, order, query: (params.get('q') || '').slice(0, 120), page: Number.isSafeInteger(page) && page > 0 ? page : 1 };
  }
  function queryString(filter) {
    const params = new URLSearchParams();
    if (filter.type !== 'todos') params.set('tipo', filter.type);
    if (filter.state !== 'todos') params.set('estado', filter.state);
    if (filter.query) params.set('q', filter.query);
    if (filter.order !== 'recientes') params.set('orden', filter.order);
    if (filter.page > 1) params.set('pagina', filter.page);
    return params.toString();
  }
  window.TrustAdminLoans = { parties, types, states, records, money, number, date, escape, name, amount, badge, totals, filters, queryString };
})();
