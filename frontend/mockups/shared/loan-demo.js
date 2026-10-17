/* Estado ficticio del mockup. No conecta wallets ni ejecuta pagos reales. */
(() => {
  const key = 'trust.mockup.agreements.v1';
  const accounts = {
    esquina: { name: 'La Esquina', role: 'prestatario', balance: 1200000, credit: 8000000 },
    taller: { name: 'Taller Central', role: 'prestatario', balance: 800000, credit: 6000000 },
    aliado: { name: 'Aliado Capital', role: 'prestamista', balance: 12000000 },
    camila: { name: 'Camila Moreno', role: 'prestamista', balance: 8000000 },
    horizonte: { name: 'Horizonte Capital', role: 'prestamista', balance: 20000000 }
  };
  const listings = {
    'oferta-aliado': { kind: 'oferta', owner: 'aliado', amount: 5000000, rate: 18, share: 10, term: 12, note: 'Financiación para capital de trabajo. El pago se realiza con el porcentaje acordado de las ventas de tu negocio.' },
    'oferta-camila': { kind: 'oferta', owner: 'camila', amount: 3000000, rate: 16, share: 8, term: 12, note: 'Capital para fortalecer tu negocio, con aportes al crédito desde cada venta.' },
    'oferta-horizonte': { kind: 'oferta', owner: 'horizonte', amount: 10000000, rate: 20, share: 12, term: 12, note: 'Financiación para ampliar la operación de tu negocio.' },
    'solicitud-esquina': { kind: 'solicitud', owner: 'esquina', amount: 5000000, rate: 18, share: 10, term: 12, note: 'Buscamos renovar el inventario de la tienda y ampliar los productos disponibles para nuestros clientes.' },
    'solicitud-taller': { kind: 'solicitud', owner: 'taller', amount: 3000000, rate: 16, share: 8, term: 12, note: 'Necesitamos herramientas y repuestos para atender más servicios en el taller.' }
  };
  const money = value => '$' + new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 }).format(value);
  const rate = value => new Intl.NumberFormat('es-CO', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value) + ' % E.A.';
  function examples() {
    return [
      ['DEMO-R01', 'solicitud-esquina', 'esquina', 'aliado', 'aliado', 20],
      ['DEMO-R02', 'solicitud-esquina', 'esquina', 'camila', 'camila', 95],
      ['DEMO-R03', 'oferta-aliado', 'taller', 'aliado', 'taller', 35],
      ['DEMO-R04', 'oferta-aliado', 'esquina', 'aliado', 'esquina', 180]
    ].map(([id, listing, borrower, lender, sender, minutes]) => ({ id, listing, borrower, lender, sender, status: 'pending', created: new Date(Date.now() - minutes * 60000).toISOString() }));
  }
  function read() {
    try {
      if (localStorage.getItem(key) === null) localStorage.setItem(key, JSON.stringify(examples()));
      const data = JSON.parse(localStorage.getItem(key) || '[]');
      if (!Array.isArray(data) || !data.every(t => t && listings[t.listing] && accounts[t.borrower]?.role === 'prestatario' && accounts[t.lender]?.role === 'prestamista' && [t.borrower, t.lender].includes(t.sender) && [t.borrower, t.lender].includes(listings[t.listing].owner) && typeof t.id === 'string' && /^[a-zA-Z0-9-]+$/.test(t.id) && ['pending', 'signed', 'rejected'].includes(t.status) && Number.isFinite(Date.parse(t.created)) && (t.status !== 'signed' || Number.isFinite(Date.parse(t.signed))))) throw new Error();
      return data;
    } catch { throw new Error('No pudimos leer la demostración guardada. Habilita el almacenamiento local o abre el mockup desde un servidor local.'); }
  }
  const recipient = t => t.sender === t.borrower ? t.lender : t.borrower;
  function balance(id, data = read()) {
    return data.reduce((value, t) => t.status !== 'signed' ? value : value + (t.borrower === id ? listings[t.listing].amount : t.lender === id ? -listings[t.listing].amount : 0), accounts[id].balance);
  }
  function credit(id, data = read()) {
    return accounts[id].credit - data.reduce((sum, t) => sum + (t.borrower === id && t.status === 'signed' ? listings[t.listing].amount : 0), 0);
  }
  function blocked(listing, borrower, lender, data = read()) {
    if (listing.amount > credit(borrower, data)) return `El monto supera el cupo disponible de ${accounts[borrower].name}: ${money(credit(borrower, data))} COP.`;
    if (listing.amount > balance(lender, data)) return 'El prestamista no tiene saldo disponible suficiente para este desembolso.';
    return '';
  }
  async function mutate(change) {
    const commit = () => {
      const data = read();
      const result = change(data);
      try { localStorage.setItem(key, JSON.stringify(data)); }
      catch { throw new Error('No fue posible guardar el cambio. Habilita el almacenamiento local y vuelve a intentarlo; no se ha confirmado la operación.'); }
      window.dispatchEvent(new Event('trust-loans-change'));
      return result;
    };
    return navigator.locks ? navigator.locks.request(key, commit) : commit();
  }
  async function send(listingId, sender) {
    return mutate(data => {
      const listing = listings[listingId];
      if (!listing || accounts[sender]?.role !== (listing.kind === 'oferta' ? 'prestatario' : 'prestamista')) throw new Error('Esta acción no corresponde al perfil de la cuenta.');
      const borrower = listing.kind === 'oferta' ? sender : listing.owner;
      const lender = listing.kind === 'oferta' ? listing.owner : sender;
      const existing = data.find(t => t.listing === listingId && t.borrower === borrower && t.lender === lender);
      if (existing) return existing;
      const reason = blocked(listing, borrower, lender, data);
      if (reason) throw new Error(reason);
      const t = { id: 'AC-' + crypto.randomUUID().slice(0, 8).toUpperCase(), listing: listingId, borrower, lender, sender, status: 'pending', created: new Date().toISOString() };
      data.push(t);
      return t;
    });
  }
  async function loadExamples() {
    return mutate(data => {
      examples().forEach(example => {
        if (!data.some(t => t.id === example.id || (t.listing === example.listing && t.borrower === example.borrower && t.lender === example.lender))) data.push(example);
      });
    });
  }
  async function respond(id, actor, approve) {
    return mutate(data => {
      const t = data.find(item => item.id === id);
      if (!t || actor !== recipient(t)) throw new Error('Solo la contraparte puede responder esta propuesta.');
      if (t.status !== 'pending') return t;
      if (approve) {
        const reason = blocked(listings[t.listing], t.borrower, t.lender, data);
        if (reason) throw new Error(reason);
        t.status = 'signed';
        t.signed = new Date().toISOString();
      } else { t.status = 'rejected'; t.rejected = new Date().toISOString(); }
      return t;
    });
  }
  function currentAccount(role) {
    const id = new URLSearchParams(location.search).get('account');
    return accounts[id]?.role === role ? id : role === 'prestamista' ? 'aliado' : 'esquina';
  }
  const portal = (id, history = false) => `../portal-prestamos/${accounts[id].role === 'prestamista' ? 'prestamista' : 'index'}.html?account=${id}${history ? '#historial' : ''}`;
  const wallet = id => `../wallet/${accounts[id].role === 'prestamista' ? 'prestamista' : 'index'}.html?account=${id}`;
  const detail = (t, actor) => `../detalle-prestamo/${listings[t.listing].kind}.html?id=${t.listing}&account=${t.sender}&transaction=${t.id}${actor !== t.sender ? '&party=recipient' : ''}`;
  window.TrustLoans = { key, accounts, listings, money, rate, read, recipient, balance, credit, blocked, send, respond, currentAccount, portal, wallet, detail, loadExamples };
})();
