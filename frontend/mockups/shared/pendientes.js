/* Avisos de la demostración: pendientes de respuesta y resultados para el remitente. */
(() => {
  const D = TrustLoans;
  const seenKey = 'trust.mockup.seen.v1';
  function seen() {
    try { return JSON.parse(localStorage.getItem(seenKey) || '{}') || {}; }
    catch { return {}; }
  }
  const isNew = (t, actor) => !seen()[`${actor}:${t.id}:proposal`];
  function mark(t, actor, type = 'proposal') {
    const data = seen(), key = `${actor}:${t.id}:${type}`;
    if (data[key]) return;
    data[key] = true;
    try { localStorage.setItem(seenKey, JSON.stringify(data)); }
    catch { return; }
    window.dispatchEvent(new Event('trust-inbox-change'));
  }
  function currentActor(data) {
    const params = new URLSearchParams(location.search);
    const record = data.find(t => t.id === params.get('transaction'));
    if (record && params.get('party') === 'recipient') return D.recipient(record);
    const role = document.body.dataset.profile || (document.body.dataset.mode === 'ofrecer' || document.body.dataset.kind === 'solicitud' ? 'prestamista' : 'prestatario');
    return D.currentAccount(role);
  }
  const topbar = document.querySelector('.topbar');
  const link = document.createElement('a');
  link.className = 'pending-access';
  link.innerHTML = '<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg><span>Pendientes</span><span class="pending-count">0</span>';
  topbar.append(link);
  const updates = document.createElement('section');
  updates.className = 'proposal-updates';
  updates.setAttribute('aria-label', 'Respuestas a tus propuestas');
  updates.hidden = true;
  document.querySelector('.page-heading').after(updates);
  function refresh() {
    try {
      const data = D.read(), actor = currentActor(data);
      const pending = data.filter(t => D.recipient(t) === actor && t.status === 'pending');
      link.href = D.portal(actor) + '#recibidas';
      link.querySelector('.pending-count').textContent = pending.length;
      link.classList.toggle('has-pending', pending.length > 0);
      link.setAttribute('aria-label', `Pendientes: ${pending.length} propuestas por responder`);
      const replies = data.filter(t => t.sender === actor && t.status !== 'pending' && !seen()[`${actor}:${t.id}:${t.status}`]);
      updates.replaceChildren();
      updates.hidden = replies.length === 0;
      replies.forEach(t => {
        const row = document.createElement('div'); row.className = 'proposal-update';
        const copy = document.createElement('p');
        const name = D.accounts[D.recipient(t)].name;
        copy.textContent = `${name} ${t.status === 'signed' ? 'aceptó' : 'rechazó'} tu propuesta de ${D.money(D.listings[t.listing].amount)} COP.`;
        const detail = document.createElement('a'); detail.href = D.detail(t, actor); detail.textContent = 'Ver respuesta';
        const dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.className = 'icon-button'; dismiss.textContent = '×'; dismiss.setAttribute('aria-label', `Marcar como vista la respuesta de ${name}`);
        dismiss.addEventListener('click', () => mark(t, actor, t.status));
        row.append(copy, detail, dismiss); updates.append(row);
      });
    } catch {
      link.href = '#contenido';
      link.setAttribute('aria-label', 'No se pudieron consultar los pendientes');
      link.querySelector('.pending-count').textContent = '—';
      updates.hidden = true;
    }
  }
  window.TrustInbox = { isNew, mark, refresh };
  window.addEventListener('storage', refresh);
  window.addEventListener('pageshow', refresh);
  window.addEventListener('trust-loans-change', refresh);
  window.addEventListener('trust-inbox-change', refresh);
  // Wait for account-specific page scripts to finish setting navigation links.
  queueMicrotask(refresh);
})();
