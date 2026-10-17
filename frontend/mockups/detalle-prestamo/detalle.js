(() => {
  const D = window.TrustLoans;
  const $ = selector => document.querySelector(selector);
  const text = (selector, value) => { $(selector).textContent = value; };
  const params = new URLSearchParams(location.search);
  const kind = document.body.dataset.kind;
  const listingId = params.get('id') || (kind === 'oferta' ? 'oferta-aliado' : 'solicitud-esquina');
  const listing = D.listings[listingId];
  const initials = name => name.split(' ').map(word => word[0]).slice(0, 2).join('');
  const date = value => new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
  let actor, sender, transaction, action, busy = false;
  const dialog = $('#confirm-dialog');
  function showError(selector, message) { text(selector, message); $(selector).hidden = false; }
  function refresh(focus = false) {
    try {
      if (!listing || listing.kind !== kind) throw new Error('No encontramos esta publicación. Vuelve al portal para elegir una oferta o solicitud.');
      const data = D.read();
      sender = D.currentAccount(kind === 'oferta' ? 'prestatario' : 'prestamista');
      const borrower = kind === 'oferta' ? sender : listing.owner;
      const lender = kind === 'oferta' ? listing.owner : sender;
      const id = params.get('transaction');
      transaction = id ? data.find(t => t.id === id && t.listing === listingId && t.sender === sender) : data.find(t => t.listing === listingId && t.borrower === borrower && t.lender === lender);
      if (id && !transaction) throw new Error('Este acuerdo no está disponible en este navegador. Ábrelo desde el historial de la demostración.');
      const received = params.get('party') === 'recipient';
      if (received && !transaction) throw new Error('Primero debe enviarse una propuesta para que la contraparte pueda responder.');
      actor = received ? D.recipient(transaction) : sender;
      const account = D.accounts[actor];
      if (received && transaction) TrustInbox.mark(transaction, actor);
      if (!received && transaction && transaction.status !== 'pending') TrustInbox.mark(transaction, actor, transaction.status);
      const other = actor === borrower ? lender : borrower;
      const pending = transaction?.status === 'pending';
      const signed = transaction?.status === 'signed';
      const rejected = transaction?.status === 'rejected';
      const canAct = !transaction || (pending && received);
      const reason = !signed && !rejected ? D.blocked(listing, borrower, lender, data) : '';
      $('#load-error').hidden = true;
      $('#detail-content').hidden = false;
      document.body.dataset.profile = account.role;
      text('.account strong', account.name);
      text('.account span', account.role === 'prestatario' ? 'Prestatario' : 'Prestamista');
      text('.account .avatar', initials(account.name));
      text('.account-note', account.role === 'prestatario' ? 'Cuenta de comercio' : 'Cuenta de prestamista');
      document.querySelectorAll('.sidebar-brand a, .main-nav a:not([data-wallet]), #back-portal').forEach(a => { a.href = D.portal(actor); });
      $('[data-wallet]').href = D.wallet(actor);
      $('#back-portal').href = D.portal(actor) + (received ? '#recibidas' : '');
      text('#back-label', received ? 'Volver a pendientes' : 'Volver al portal');
      text('#record-link-label', pending ? 'Ver en mis pendientes' : 'Ver en mi historial');
      $('#history-link').href = pending
        ? `${D.portal(actor)}&record=${transaction.id}${received ? '' : '&inbox=waiting'}#recibidas`
        : D.portal(actor, true).replace('#historial', transaction ? `&record=${transaction.id}#historial` : '#historial');
      text('#detail-title', signed ? 'Préstamo firmado' : rejected ? 'Propuesta rechazada' : received ? (kind === 'oferta' ? 'Solicitud recibida' : 'Oferta recibida') : kind === 'oferta' ? 'Ver oferta' : 'Ver solicitud');
      text('#detail-intro', signed ? 'Ambas partes aceptaron las condiciones. El acuerdo ya está en tu historial.' : received ? `${D.accounts[sender].name} te envió esta propuesta de préstamo.` : kind === 'oferta' ? 'Revisa las condiciones antes de enviar tu solicitud.' : 'Revisa las condiciones antes de ofrecer financiación a este comercio.');
      document.title = `Trust — ${$('#detail-title').textContent} · Órbita`;
      text('#detail-state', signed ? 'Al día' : rejected ? 'Sin firma' : pending ? (received ? 'Por responder' : 'Esperando respuesta') : reason ? 'No disponible' : 'Disponible');
      text('#counterparty-name', D.accounts[other].name);
      text('#counterparty-role', D.accounts[other].role === 'prestatario' ? 'Prestatario' : 'Prestamista');
      text('#counterparty-avatar', initials(D.accounts[other].name));
      text('#listing-reference', transaction?.id || (kind === 'oferta' ? 'Oferta publicada' : 'Solicitud publicada'));
      text('#amount-label', transaction ? 'Monto del acuerdo' : kind === 'oferta' ? 'Monto ofrecido' : 'Monto solicitado');
      text('#detail-amount', D.money(listing.amount));
      text('#interest-label', !transaction ? (kind === 'solicitud' ? 'Tasa máxima propuesta' : 'Tasa de interés') : 'Tasa de interés del acuerdo');
      text('#detail-rate', D.rate(listing.rate));
      text('#detail-share', `${listing.share} % de cada venta`);
      text('#detail-term', `${listing.term} meses`);
      text('#term-label', signed ? 'Plazo acordado' : 'Plazo propuesto');
      text('#rate-note', kind === 'solicitud' ? `Esta oferta de financiación mantiene la tasa propuesta de ${D.rate(listing.rate)}. El porcentaje de ventas es independiente del interés.` : 'La tasa de interés y el porcentaje de ventas son condiciones distintas.');
      text('#purpose-title', kind === 'oferta' ? 'Sobre esta oferta' : 'Destino del préstamo');
      text('#detail-note', listing.note);
      text('#split-business', D.money(100000 - listing.share * 1000));
      text('#split-loan', D.money(listing.share * 1000));
      $('#split-business-bar').style.width = `${100 - listing.share}%`;
      text('#availability-label', account.role === 'prestatario' ? 'Tu cupo disponible' : 'Tu saldo disponible');
      text('#availability-value', D.money(account.role === 'prestatario' ? D.credit(actor, data) : D.balance(actor, data)) + ' COP');
      text('#availability-caption', account.role === 'prestatario' ? 'Cupo ilustrativo de Trust' : 'Saldo de demostración');
      text('#next-explanation', signed ? 'El préstamo inicia Al día. Puedes seguir su estado desde el historial del portal.' : 'Enviar una propuesta registra la aceptación del remitente. La contraparte debe aprobar las mismas condiciones para firmar y desembolsar.');
      const progress = signed ? 2 : transaction ? 1 : 0;
      document.querySelectorAll('.agreement-progress li').forEach((li, index) => {
        li.removeAttribute('aria-current');
        if (index === progress) li.setAttribute('aria-current', 'step');
        li.querySelector('span').textContent = index < progress ? '✓' : index + 1;
      });
      text('#agreement-title', signed ? 'El acuerdo está completo' : rejected ? 'La propuesta no fue aceptada' : pending ? received ? 'Tu aprobación completa el acuerdo' : 'Esperando a la contraparte' : 'Tu siguiente paso');
      text('#agreement-description', signed ? `${transaction.id} · Firmado el ${date(transaction.signed)}.` : rejected ? 'No se firmó un préstamo ni se registró un desembolso. La propuesta queda en el historial sin estado de pago.' : pending ? received ? 'El remitente ya aceptó estas condiciones. Revisa el acuerdo y confirma si deseas firmarlo.' : `${D.accounts[other].name} debe aprobar estas condiciones. Aún no hay un préstamo firmado ni movimientos en las cuentas.` : kind === 'oferta' ? `Envía tu solicitud a ${D.accounts[other].name} por el monto y las condiciones de esta oferta.` : `Envía una oferta a ${D.accounts[other].name} por el monto y las condiciones de esta solicitud.`);
      $('#agreement-form').hidden = !canAct;
      $('#primary-action').hidden = !canAct;
      $('#primary-action').disabled = Boolean(reason);
      $('#accept-terms').disabled = Boolean(reason);
      text('#eligibility-note', reason);
      text('#primary-action', received ? 'Aceptar y firmar' : kind === 'oferta' ? 'Solicitar préstamo' : 'Ofertar préstamo');
      text('#consent-text', received ? 'Acepto las condiciones de este acuerdo y autorizo la firma y el desembolso simulado.' : 'Acepto este monto, tasa, plazo y porcentaje de ventas. Si la contraparte aprueba, el préstamo quedará firmado y se simulará el desembolso.');
      $('#reject-action').hidden = !(pending && received);
      $('#acceptances').hidden = !transaction;
      $('#acceptances').replaceChildren();
      if (transaction) {
        [[sender, true, transaction.created], [D.recipient(transaction), signed, transaction.signed]].forEach(([id, accepted, timestamp]) => {
          const row = document.createElement('div'); row.className = 'acceptance';
          const mark = document.createElement('span'); mark.textContent = accepted ? '✓' : rejected ? '×' : '○'; mark.setAttribute('aria-hidden', 'true');
          const content = document.createElement('div'), name = document.createElement('strong'), note = document.createElement('small');
          name.textContent = `${D.accounts[id].name} · ${accepted ? 'Condiciones aceptadas' : rejected ? 'Propuesta rechazada' : 'Por aprobar'}`;
          note.textContent = timestamp ? date(timestamp) : rejected ? 'El acuerdo no continúa.' : 'Sin firma de esta parte.';
          content.append(name, note); row.append(mark, content); $('#acceptances').append(row);
        });
      }
      $('#history-link').hidden = !transaction;
      $('#response-complete').hidden = !(received && (signed || rejected));
      if (received && (signed || rejected)) {
        text('#response-message', signed ? `Aceptaste la propuesta. El préstamo quedó firmado y se registró el desembolso simulado. ${D.accounts[sender].name} puede consultar tu respuesta.` : `Rechazaste la propuesta. ${D.accounts[sender].name} puede consultar tu respuesta. No se movió dinero.`);
        const next = data.filter(t => D.recipient(t) === actor && t.status === 'pending' && t.id !== transaction.id).sort((a, b) => Date.parse(b.created) - Date.parse(a.created))[0];
        $('#next-pending').hidden = !next;
        if (next) $('#next-pending').href = D.detail(next, actor);
        $('#return-received').href = D.portal(actor) + '#recibidas';
      }
      TrustInbox.refresh();
      $('#movement-section').hidden = !signed;
      if (signed) {
        text('#movement-reference', `${transaction.id} · ${date(transaction.signed)} · COP`);
        $('#movement-list').replaceChildren();
        [[lender, 'Débito al prestamista', '−', 'debit'], [borrower, 'Abono al prestatario', '+', 'credit']].forEach(([id, label, sign, cls]) => {
          const row = document.createElement('div'); row.className = 'movement ' + cls;
          const content = document.createElement('div'), name = document.createElement('strong'), note = document.createElement('small'), amount = document.createElement('span');
          name.textContent = D.accounts[id].name; note.textContent = label; amount.textContent = sign + D.money(listing.amount);
          content.append(name, note); row.append(content, amount); $('#movement-list').append(row);
        });
        const preceding = data.filter(t => t.status === 'signed' && (t.signed < transaction.signed || (t.signed === transaction.signed && data.indexOf(t) < data.indexOf(transaction))));
        const before = D.balance(actor, preceding);
        text('#balance-before', D.money(before));
        text('#balance-after', D.money(before + (actor === borrower ? listing.amount : -listing.amount)));
      }
      $('#demo-toolbar').hidden = !transaction;
      if (transaction) {
        text('#demo-context', `Revisión del mockup · Cuenta representada: ${account.name} (${account.role})`);
        const next = received ? sender : D.recipient(transaction);
        $('#demo-switch').href = D.detail(transaction, next);
        text('#demo-switch', `Revisar como ${D.accounts[next].name}`);
      }
      if (focus) $('#agreement-title').focus();
    } catch (error) {
      showError('#load-error', error.message);
      $('#detail-content').hidden = true;
      $('#demo-toolbar').hidden = true;
    }
  }
  function confirm(nextAction) {
    action = nextAction;
    $('#confirm-error').hidden = true;
    text('#confirm-title', action === 'send' ? (kind === 'oferta' ? '¿Enviar solicitud de préstamo?' : '¿Enviar oferta de préstamo?') : action === 'approve' ? '¿Firmar y desembolsar?' : '¿Rechazar esta propuesta?');
    text('#confirm-description', action === 'send' ? 'Registrarás tu aceptación de estas condiciones. El dinero se desembolsará únicamente cuando la contraparte apruebe.' : action === 'approve' ? 'Ambas aceptaciones quedarán registradas. Se simulará el débito al prestamista y el abono al prestatario por el capital indicado.' : 'La propuesta quedará rechazada en el historial. No se firmará un préstamo ni se moverán saldos.');
    text('#commit-action', action === 'send' ? 'Confirmar envío' : action === 'approve' ? 'Confirmar firma y desembolso' : 'Confirmar rechazo');
    $('#confirmation-terms').replaceChildren();
    [['Prestatario', D.accounts[transaction?.borrower || (kind === 'oferta' ? sender : listing.owner)].name], ['Prestamista', D.accounts[transaction?.lender || (kind === 'oferta' ? listing.owner : sender)].name], ['Capital', D.money(listing.amount) + ' COP'], ['Interés', D.rate(listing.rate)], ['De cada venta', listing.share + ' %'], ['Plazo', listing.term + ' meses']].forEach(([label, value]) => {
      const row = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label; dd.textContent = value; row.append(dt, dd); $('#confirmation-terms').append(row);
    });
    dialog.showModal();
    $('#cancel-confirm').focus();
  }
  $('#agreement-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!$('#accept-terms').checked || $('#primary-action').disabled) return;
    confirm(transaction ? 'approve' : 'send');
  });
  $('#reject-action').addEventListener('click', () => confirm('reject'));
  [$('#close-confirm'), $('#cancel-confirm')].forEach(button => button.addEventListener('click', () => { if (!busy) dialog.close(); }));
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  $('#commit-action').addEventListener('click', async () => {
    if (busy) return;
    busy = true; $('#commit-action').disabled = true;
    try {
      const t = action === 'send' ? await D.send(listingId, actor) : await D.respond(transaction.id, actor, action === 'approve');
      params.set('id', listingId); params.set('account', sender); params.set('transaction', t.id);
      history.replaceState(null, '', `${location.pathname}?${params}`);
      $('#accept-terms').checked = false;
      dialog.close();
      refresh(true);
    } catch (error) { showError('#confirm-error', error.message); }
    finally { busy = false; $('#commit-action').disabled = false; }
  });
  window.addEventListener('storage', event => { if (event.key === D.key) { if (dialog.open) dialog.close(); $('#accept-terms').checked = false; refresh(); } });
  window.addEventListener('pageshow', () => refresh());
  refresh();
})();
