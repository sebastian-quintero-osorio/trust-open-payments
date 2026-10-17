(() => {
  const D = TrustLoans, id = portalAccount, account = D.accounts[id];
  const $ = selector => document.querySelector(selector);
  $('.account strong').textContent = account.name;
  $('.account .avatar').textContent = account.name.split(' ').map(w => w[0]).slice(0, 2).join('');
  $('[data-wallet]').href = D.wallet(id);
  const create = $('.page-heading > a');
  create.href += `?account=${id}`;
  if (!['esquina', 'aliado'].includes(id)) document.querySelectorAll('.history-table tbody tr').forEach(row => row.remove());
  const references = [...document.querySelectorAll('[data-listing]')];
  references.forEach(link => { link.href += `&account=${id}`; });
  let receivedFilter = new URLSearchParams(location.search).get('inbox') === 'waiting' ? 'waiting' : 'pending';
  document.querySelectorAll('[data-received-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.receivedFilter === receivedFilter)));
  const noun = isLender ? 'solicitud' : 'oferta';
  const plural = isLender ? 'solicitudes' : 'ofertas';
  function element(tag, cls, copy) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (copy !== undefined) node.textContent = copy;
    return node;
  }
  function relativeTime(value, waiting = false) {
    const minutes = Math.max(0, Math.floor((Date.now() - new Date(value)) / 60000));
    if (minutes < 1) return waiting ? 'Enviada ahora' : 'Recibida ahora';
    if (minutes < 60) return `Hace ${minutes} min`;
    if (minutes < 1440) return `Hace ${Math.floor(minutes / 60)} h`;
    return new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short' }).format(new Date(value));
  }
  function renderReceived(data) {
    const received = data.filter(t => D.recipient(t) === id).sort((a, b) => Date.parse(b.created) - Date.parse(a.created));
    const pending = received.filter(t => t.status === 'pending');
    $('#approval-inbox').hidden = pending.length === 0;
    $('#inbox-title').textContent = `Tienes ${pending.length} ${pending.length === 1 ? noun : plural} por responder`;
    $('#inbox-link').textContent = `Revisar ${plural}`;
    $('#received-title').textContent = 'Pendientes';
    $('#received-intro').textContent = isLender ? 'Estos comercios quieren financiarse contigo. Revisa cada solicitud antes de decidir.' : 'Estos prestamistas quieren financiar tu negocio. Revisa cada oferta antes de decidir.';
    $('#received-count').textContent = pending.length;
    $('#tab-recibidas').setAttribute('aria-label', `Pendientes: ${pending.length} por responder`);
    $('#pending-filter-count').textContent = pending.length;
    const waiting = receivedFilter === 'waiting';
    const visible = waiting
      ? data.filter(t => t.sender === id && t.status === 'pending').sort((a, b) => Date.parse(b.created) - Date.parse(a.created))
      : received.filter(t => receivedFilter === 'pending' ? t.status === 'pending' : t.status !== 'pending');
    if (waiting) $('#received-intro').textContent = 'Ya aceptaste estas condiciones. La otra parte debe responder para completar el acuerdo.';
    const fresh = pending.filter(t => TrustInbox.isNew(t, id)).length;
    $('#received-summary').textContent = receivedFilter === 'pending' ? `${pending.length} por responder · ${fresh} ${fresh === 1 ? 'nueva' : 'nuevas'}` : waiting ? `${visible.length} ${visible.length === 1 ? 'propuesta esperando respuesta' : 'propuestas esperando respuesta'}` : `${visible.length} ${visible.length === 1 ? 'propuesta respondida' : 'propuestas respondidas'}`;
    $('#inbox-list').replaceChildren();
    $('#inbox-list').hidden = visible.length === 0;
    $('#received-empty').hidden = visible.length > 0;
    $('#received-empty-title').textContent = receivedFilter === 'pending' ? 'No tienes propuestas por responder' : waiting ? 'No tienes propuestas esperando respuesta' : 'Aún no has respondido propuestas';
    $('#received-empty-copy').textContent = receivedFilter === 'pending' ? 'Cuando recibas una propuesta, aparecerá aquí. Mientras tanto, puedes explorar préstamos.' : waiting ? 'Cuando envíes una oferta o solicitud, podrás seguir aquí la respuesta de la otra parte.' : 'Aquí podrás volver a consultar las propuestas que aceptes o rechaces.';
    visible.forEach(t => {
      const listing = D.listings[t.listing], sender = D.accounts[waiting ? D.recipient(t) : t.sender];
      const fresh = !waiting && t.status === 'pending' && TrustInbox.isNew(t, id);
      const card = element('article', 'received-card' + (fresh ? ' is-new' : ''));
      const heading = element('div', 'received-card-head');
      const avatar = element('span', 'lender-avatar', sender.name.split(' ').map(w => w[0]).slice(0, 2).join(''));
      avatar.setAttribute('aria-hidden', 'true');
      const identity = element('div', 'received-identity');
      const name = element('h3', '', sender.name); name.id = `received-${t.id}`;
      card.setAttribute('aria-labelledby', name.id);
      if (fresh) name.append(element('span', 'new-label', 'Nueva'));
      identity.append(name, element('p', '', waiting ? `${isLender ? 'Oferta enviada' : 'Solicitud enviada'} · ${t.id}` : `En respuesta a tu ${isLender ? 'oferta' : 'solicitud'} · ${t.id}`));
      const time = element('time', 'received-time', relativeTime(t.created, waiting)); time.dateTime = t.created;
      time.title = new Intl.DateTimeFormat('es-CO', { dateStyle: 'long', timeStyle: 'short' }).format(new Date(t.created));
      heading.append(avatar, identity, time);
      const terms = element('dl', 'received-terms');
      [
        [waiting ? (isLender ? 'Monto ofrecido' : 'Monto solicitado') : isLender ? 'Monto solicitado' : 'Monto ofrecido', D.money(listing.amount), 'COP', 'received-amount'],
        ['Tasa de interés', D.rate(listing.rate).replace(' E.A.', ''), 'Efectiva anual (E.A.)', 'received-rate'],
        ['Plazo', `${listing.term} meses`, 'Propuesto', ''],
        ['De cada venta', `${listing.share} %`, 'Para pagar el crédito', '']
      ].forEach(([label, value, hint, cls]) => {
        const group = element('div'), dd = element('dd', cls, value);
        dd.append(element('small', '', hint)); group.append(element('dt', '', label), dd); terms.append(group);
      });
      const footer = element('div', 'received-card-foot');
      const state = t.status === 'pending' ? (waiting ? 'Esperando respuesta' : 'Por responder') : t.status === 'signed' ? 'Aceptada · Firmado' : 'Rechazada';
      const action = element('a', 'button button-detail', waiting ? 'Ver propuesta' : t.status === 'pending' ? `Revisar ${noun}` : 'Ver respuesta');
      action.href = D.detail(t, id); action.setAttribute('aria-label', waiting ? `Ver propuesta enviada a ${sender.name}` : `${action.textContent} de ${sender.name}`);
      footer.append(element('span', 'received-status', state), action);
      card.append(heading, terms);
      const reason = t.status === 'pending' ? D.blocked(listing, t.borrower, t.lender, data) : '';
      if (reason) card.append(element('p', 'received-blocked', `No se puede aceptar por ahora. ${reason}`));
      card.append(footer); $('#inbox-list').append(card);
    });
  }
  document.querySelectorAll('[data-received-filter]').forEach(button => button.addEventListener('click', () => {
    receivedFilter = button.dataset.receivedFilter;
    document.querySelectorAll('[data-received-filter]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    refresh();
  }));
  $('#load-examples').addEventListener('click', async () => {
    try { await D.loadExamples(); refresh(); $('#examples-status').textContent = 'Ejemplos disponibles. Los acuerdos existentes se conservan.'; }
    catch (error) { $('#examples-status').textContent = error.message; }
  });
  function refresh() {
    try {
      const data = D.read();
      $('#demo-state-error').hidden = true;
      document.querySelectorAll('[data-local-agreement]').forEach(row => row.remove());
      data.filter(t => t.borrower === id || t.lender === id).slice().reverse().forEach(t => {
        const listing = D.listings[t.listing], signed = t.status === 'signed';
        const other = D.accounts[t.borrower === id ? t.lender : t.borrower];
        const row = document.createElement('tr');
        row.dataset.localAgreement = t.id;
        row.dataset.kind = signed ? 'firmado' : isLender ? 'ofrecido' : 'solicitado';
        row.dataset.state = signed ? 'al-dia' : '';
        const heading = document.createElement('th'); heading.scope = 'row';
        const link = document.createElement('a'); link.className = 'record-id'; link.href = D.detail(t, id); link.textContent = t.id; link.setAttribute('aria-label', `Ver acuerdo ${t.id} con ${other.name}`);
        const name = document.createElement('span'); name.className = 'record-name'; name.textContent = other.name;
        heading.append(link, name); row.append(heading);
        const cells = [
          ['Monto', D.money(listing.amount), 'COP', 'history-amount'],
          ['Tasa de interés', D.rate(listing.rate), '', 'history-rate'],
          ['De cada venta', `${listing.share} %`, 'Para pagar el crédito', ''],
          [signed ? 'Fecha de firma' : 'Fecha de propuesta', new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium' }).format(new Date(t.signed || t.created)), '', ''],
          ['Estado', signed ? 'Al día' : t.status === 'rejected' ? 'Rechazado' : 'Por responder', signed ? 'Firmado por ambas partes' : t.status === 'rejected' ? '' : D.recipient(t) === id ? 'Tu respuesta' : `Esperando a ${other.name}`, 'loan-state ' + (signed ? 'state-al-dia' : 'state-sin-firma')]
        ];
        cells.forEach(([label, value, hint, cls]) => {
          const cell = document.createElement('td'), main = document.createElement('span'); cell.dataset.label = label; main.className = cls; main.textContent = value; cell.append(main);
          if (hint) {
            const awaitingResponse = label === 'Estado' && t.status === 'pending';
            const note = document.createElement(awaitingResponse ? 'span' : 'small');
            if (awaitingResponse) {
              note.className = 'response-hint';
            }
            const copy = document.createElement('span'); copy.textContent = hint;
            note.append(copy); cell.append(note);
          }
          row.append(cell);
        });
        $('.history-table tbody').append(row);
      });
      renderReceived(data);
      if (!isLender) {
        const credit = D.credit(id, data);
        $('.credit-amount').replaceChildren(document.createTextNode(D.money(credit)));
        const unit = document.createElement('span'); unit.textContent = 'COP'; $('.credit-amount').append(unit);
        references.forEach(link => { link.closest('.offer-row').dataset.eligible = String(D.listings[link.dataset.listing].amount <= credit); });
        document.querySelector('[data-filter][aria-pressed="true"]').click();
      }
      const selected = new URLSearchParams(location.search).get('record');
      const record = data.find(t => t.id === selected && (t.borrower === id || t.lender === id));
      if (record && !refresh.initialized) {
        historyType.value = record.status === 'signed' ? 'firmado' : isLender ? 'ofrecido' : 'solicitado';
        historySearch.value = record.id;
      }
      refresh.initialized = true;
      filterHistory();
    } catch (error) { $('#demo-state-error').hidden = false; $('#demo-state-error').textContent = error.message; }
  }
  window.addEventListener('storage', event => { if (event.key === D.key) refresh(); });
  window.addEventListener('pageshow', refresh);
  window.addEventListener('trust-loans-change', refresh);
  window.addEventListener('trust-inbox-change', refresh);
  refresh();
})();
