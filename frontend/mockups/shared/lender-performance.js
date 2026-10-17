/* Rendimiento histórico ilustrativo, compartido por wallet e historial.
   Los intereses cobrados ya forman parte del saldo inicial; no se abonan de nuevo. */
(() => {
  const D = TrustLoans;
  const panels = [...document.querySelectorAll('[data-lender-performance]')];
  if (!panels.length || document.body.dataset.profile !== 'prestamista') return;
  const id = D.currentAccount('prestamista');
  // Los tres firmados del historial de Aliado Capital, incluido el préstamo pagado.
  const history = id === 'aliado' ? [
    { id: 'TR-1048', rate: 18, interestReceived: 18000 },
    { id: 'TR-1031', rate: 16, interestReceived: 42000 },
    { id: 'TR-0982', rate: 15, interestReceived: 160000 }
  ] : [];
  function refresh() {
    try {
      const loans = [...history, ...D.read()
        .filter(t => t.lender === id && t.status === 'signed')
        .map(t => ({ rate: D.listings[t.listing].rate, interestReceived: 0 }))];
      const earned = loans.reduce((sum, loan) => sum + loan.interestReceived, 0);
      const average = loans.length ? loans.reduce((sum, loan) => sum + loan.rate, 0) / loans.length : null;
      panels.forEach(panel => {
        panel.querySelector('[data-earned]').textContent = `${D.money(earned)} COP`;
        panel.querySelector('[data-average-rate]').textContent = average === null ? '—' : D.rate(average);
        panel.querySelector('[data-performance-note]').textContent = loans.length
          ? `Promedio simple de las tasas pactadas de ${loans.length} préstamos firmados, incluidos los pagados. No representa la rentabilidad obtenida.`
          : 'Aún no tienes préstamos firmados. La tasa promedio aparecerá cuando firmes tu primer acuerdo.';
      });
    } catch {
      panels.forEach(panel => {
        panel.querySelector('[data-earned]').textContent = '—';
        panel.querySelector('[data-average-rate]').textContent = '—';
        panel.querySelector('[data-performance-note]').textContent = 'No se pudo consultar el historial de ganancias. Revisa el almacenamiento local y recarga la vista.';
      });
    }
  }
  window.addEventListener('storage', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('trust-loans-change', refresh);
  refresh();
})();
