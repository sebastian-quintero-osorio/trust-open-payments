import { api, allPages } from '../lib/api.js';
import { $, $$, el, text, show, busy, errorMessage, safeExternal, polling } from '../lib/ui.js';
import { money, parseMoney, percent, date, name, initials, loanState } from '../lib/format.js';
import { home } from '../app.js';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export async function init(me) {
  const query = new URLSearchParams(location.search), id = query.get('loan_id') || query.get('id');
  if (!UUID.test(id || '')) throw new Error('El identificador del préstamo no es válido. Vuelve al portal y selecciona un registro.');
  const lender = me.role === 'lender', isLoan = query.has('loan_id'), offer = location.pathname.endsWith('oferta.html');
  const config = await api('getConfig');
  let loan, listing, preview, draft, selectedAction, previewVersion = 0;
  text('#interest-label', 'Cargo fijo sobre el capital'); text('#rate-note', 'El cargo fijo se aplica una sola vez al capital. El porcentaje de ventas indica cuánto se destina a pagar el crédito.');
  text('#counterparty-role', lender ? 'Prestatario' : 'Prestamista');
  $('#progress-signed').replaceChildren(el('span', '', '3'), document.createTextNode('Desembolso confirmado'));
  text('#next-explanation', 'El comercio acepta las condiciones y el prestamista autoriza el desembolso en su wallet. El servidor confirma cuándo está activo.');
  text('#consent-text', 'He leído y acepto las condiciones completas del préstamo.');
  text('#detail-note', 'Los montos y condiciones se confirman con Trust antes de crear el acuerdo.');
  show('.account-availability', false); show('#detail-content'); show('#agreement-form', false);
  text('#record-link-label', 'Ver en mi historial'); $('#history-link').href = home(me.role) + '#historial'; show('#history-link');
  const dialog = $('#confirm-dialog');
  $('#close-confirm').onclick = $('#cancel-confirm').onclick = () => dialog.close();
  function setParty(party) { text('#counterparty-name', name(party)); text('#counterparty-avatar', initials(name(party))); }
  function split(pct, example) {
    const configured = config.example_splits.find(item => item.pct_bps === pct);
    example ||= configured && { sale: config.example_sale, merchant_amount: configured.merchant, abono: configured.abono };
    text('.repayment-visual > p', example ? `Así se repartiría una venta de ${money(example.sale, true)}.` : 'Distribución vigente de cada venta.');
    text('#split-business', example ? money(example.merchant_amount) : percent(10000 - pct));
    text('#split-loan', example ? money(example.abono) : percent(pct));
    $('#split-business-bar').style.width = `${100 - pct / 100}%`;
  }
  function terms(value, example) {
    text('#detail-amount', money(value.principal)); text('#detail-rate', percent(value.fee_bps));
    text('#detail-share', percent(value.pct_initial_bps)); text('#detail-term', `${value.term_months} meses`);
    text('#detail-note', `Cargo: ${money(value.fee_amount, true)}. Total a pagar: ${money(value.total_repayable, true)}. Mínimo mensual: ${money(value.monthly_minimum, true)}.`);
    split(value.pct_initial_bps, example);
  }
  function confirm(action, label) {
    selectedAction = action; text('#confirm-title', label); text('#confirm-description', 'Comprueba las condiciones antes de continuar.');
    const dl = $('#confirmation-terms'); dl.replaceChildren();
    const value = loan?.terms || preview?.terms;
    if (value) [['Capital', money(value.principal, true)], ['Cargo fijo', percent(value.fee_bps)], ['Total a pagar', money(value.total_repayable, true)], ['Plazo', `${value.term_months} meses`], ['De cada venta', percent(loan?.pct_bps ?? value.pct_initial_bps)]].forEach(([label, amount]) => { const div = el('div'); div.append(el('dt', '', label), el('dd', '', amount)); dl.append(div); });
    text('#commit-action', label); show('#confirm-error', false); dialog.showModal();
  }
  async function responseSummary(action) {
    const messages = { accept: 'Aceptaste las condiciones. El prestamista debe autorizar el desembolso.', reject: 'Rechazaste la propuesta. No se ha realizado un desembolso.', cancel: 'Cancelaste el acuerdo. No se ha realizado un desembolso.' };
    text('#response-message', messages[action]); show('#response-complete');
    $('#return-received').href = home(me.role) + '#recibidas';
    show('#next-pending', false);
    document.dispatchEvent(new Event('trust:loans-changed'));
    try {
      const loans = await allPages('listLoans');
      const next = loans.find(item => item.id !== id && item.next_action.actor === me.role && item.next_action.type !== 'none');
      if (next) { $('#next-pending').href = `/detalle-prestamo/oferta.html?loan_id=${encodeURIComponent(next.id)}`; show('#next-pending'); }
    } catch {
      text('#response-message', `${messages[action]} No pudimos consultar el siguiente pendiente; puedes volver a la bandeja.`);
    }
  }
  $('#commit-action').onclick = () => busy($('#commit-action'), async () => {
    if (selectedAction === 'create') {
      const created = await api('createLoan', { body: draft });
      location.assign(`/detalle-prestamo/oferta.html?loan_id=${encodeURIComponent(created.id)}`);
    } else if (selectedAction === 'cancelListing') {
      await api(offer ? 'cancelLoanOffer' : 'cancelLoanRequest', { params: { [offer ? 'offer_id' : 'request_id']: id } });
      location.assign(home(me.role) + '#historial');
    } else if (selectedAction === 'disburse') {
      const response = await api('disburseLoan', { params: { loan_id: id }, body: { client: 'web' } });
      location.assign(safeExternal(response.authorization_url));
    } else if (selectedAction === 'authorize') {
      // Re-fetch to avoid opening an authorization link from stale browser state.
      const fresh = await api('getLoan', { params: { loan_id: id } });
      if (!fresh.disbursement.authorization_url) { dialog.close(); await refresh(); return; }
      location.assign(safeExternal(fresh.disbursement.authorization_url));
    } else {
      await api({ accept: 'acceptLoan', reject: 'rejectLoan', cancel: 'cancelLoan' }[selectedAction], { params: { loan_id: id } });
      dialog.close(); await refresh(); await responseSummary(selectedAction);
    }
  }, '#confirm-error');
  $('#agreement-form').onsubmit = event => {
    event.preventDefault();
    if (!$('#accept-terms').checked) { $('#accept-terms').reportValidity(); return; }
    if (loan) confirm(loan.next_action.type === 'authorize_in_wallet' ? 'authorize' : loan.next_action.type, $('#primary-action').textContent);
    else if (preview) confirm('create', lender ? 'Ofrecer préstamo' : 'Aceptar oferta');
  };
  async function refresh() {
    const previous = loan;
    loan = await api('getLoan', { params: { loan_id: id } });
    const changed = !previous || JSON.stringify([previous.status, previous.next_action, previous.terms, previous.pct_bps]) !== JSON.stringify([loan.status, loan.next_action, loan.terms, loan.pct_bps]);
    setParty(lender ? loan.merchant : loan.lender); terms(loan.terms);
    text('#detail-title', 'Detalle del préstamo'); text('#listing-reference', loan.id); text('#detail-state', loanState(loan));
    text('#amount-label', ['active', 'paid'].includes(loan.status) ? 'Capital desembolsado' : 'Capital acordado'); text('#purpose-title', 'Condiciones acordadas');
    text('#detail-intro', `Creado el ${date(loan.created_at)}`); text('#detail-share', percent(loan.pct_bps));
    split(loan.pct_bps);
    const act = loan.next_action.actor === me.role && loan.next_action.type !== 'none';
    const pending = ['pending_acceptance', 'pending_disbursement', 'disbursing'].includes(loan.status), funded = ['active', 'paid'].includes(loan.status);
    text('#record-link-label', funded ? 'Ver en mi historial' : pending ? 'Ver en mis pendientes' : 'Ver en mis acuerdos');
    $('#history-link').href = home(me.role) + (funded ? '#historial' : `?pendientes=${pending ? act ? 'pending' : 'waiting' : 'resolved'}#recibidas`);
    show('#agreement-form', act); show('#primary-action', act);
    if (changed) {
      $('#accept-terms').checked = false;
      if (dialog.open) { dialog.close(); errorMessage(new Error('El estado del acuerdo cambió. Revisa la información actualizada antes de continuar.')); }
    }
    const labels = { accept: 'Aceptar condiciones', disburse: 'Autorizar desembolso', authorize_in_wallet: 'Continuar en mi wallet' };
    text('#primary-action', labels[loan.next_action.type] || 'Continuar');
    text('#agreement-description', act ? 'Revisa las condiciones y confirma para continuar.' : ['active', 'paid'].includes(loan.status) ? `Abonado: ${money(loan.allocated, true)}. Saldo pendiente: ${money(loan.outstanding, true)}.` : `Estado: ${loanState(loan)}. El siguiente paso corresponde a ${loan.next_action.actor === 'merchant' ? 'el prestatario' : loan.next_action.actor === 'lender' ? 'el prestamista' : 'Trust'}.`);
    const canReject = (me.role === 'merchant' && loan.status === 'pending_acceptance') || (lender && loan.status === 'pending_disbursement');
    show('#reject-action', canReject); $('#reject-action').onclick = () => confirm('reject', 'Rechazar propuesta');
    let cancel = $('#cancel-loan');
    if (!cancel) { cancel = el('button', 'button button-detail', 'Cancelar acuerdo'); cancel.id = 'cancel-loan'; $('.agreement-actions').append(cancel); }
    cancel.hidden = !['pending_acceptance', 'pending_disbursement'].includes(loan.status) || me.role === 'admin';
    cancel.onclick = () => confirm('cancel', 'Cancelar acuerdo');
    show('#movement-section', loan.disbursement.status === 'completed');
    if (loan.disbursement.status === 'completed') {
      text('#movement-section .section-heading span', 'Confirmado'); text('#movement-reference', `Capital desembolsado: ${money(loan.terms.principal, true)} · ${date(loan.activated_at)}`);
      show('.balance-line', false);
    }
    $$('.agreement-progress li').forEach(li => li.removeAttribute('aria-current'));
    $(`#progress-${['active', 'paid'].includes(loan.status) ? 'signed' : 'wait'}`).setAttribute('aria-current', 'step');
  }
  if (isLoan) {
    await refresh();
    if (loan.status === 'disbursing') {
      const stop = polling(async () => { await refresh(); if (loan.status !== 'disbursing') stop(); }, 2000);
    }
    return;
  }
  listing = await api(offer ? 'getLoanOffer' : 'getLoanRequest', { params: { [offer ? 'offer_id' : 'request_id']: id } });
  const own = offer ? lender && listing.lender.id === me.id : !lender && (!listing.merchant || listing.merchant.id === me.id);
  setParty(offer ? listing.lender : listing.merchant || { business_name: me.business_name });
  text('#counterparty-role', offer ? 'Prestamista' : 'Prestatario');
  text('#detail-title', offer ? 'Ver oferta' : 'Ver solicitud');
  text('#detail-intro', offer ? 'Revisa las condiciones antes de aceptar la oferta.' : 'Revisa la solicitud antes de proponer las condiciones del préstamo.');
  text('#amount-label', offer ? 'Monto ofrecido' : 'Monto solicitado'); text('#purpose-title', offer ? 'Sobre esta oferta' : 'Sobre esta solicitud');
  text('#listing-reference', listing.id); text('#detail-state', loanState(listing));
  text('#detail-amount', `${money(listing.min_amount)} a ${money(listing.max_amount)}`);
  text('#detail-rate', offer ? percent(listing.fee_bps) : 'Por proponer'); text('#detail-term', offer ? `${listing.term_months} meses` : 'Por proponer'); text('#detail-share', percent(listing.pct_bps));
  split(listing.pct_bps);
  text('#agreement-description', own ? 'Esta publicación pertenece a tu cuenta.' : 'Elige el monto y consulta las condiciones completas antes de aceptar.');
  show('#primary-action', false);
  if (own) {
    show('#reject-action', listing.status === 'open'); text('#reject-action', 'Cancelar publicación'); $('#reject-action').onclick = () => confirm('cancelListing', 'Cancelar publicación'); return;
  }
  if (listing.status !== 'open') { text('#agreement-description', 'Esta publicación ya no está disponible.'); return; }
  const editor = el('form'); editor.id = 'proposal-editor';
  function field(id, label, value) {
    const wrap = el('label', 'app-field', label), input = el('input'); input.id = id; input.value = value; input.required = true; input.inputMode = 'decimal'; wrap.append(input); editor.append(wrap); return input;
  }
  const initial = listing.proposal?.amount || listing.min_amount;
  const amount = field('proposal-amount', 'Monto del acuerdo (COP)', money(initial).replace('$', ''));
  const fee = lender ? field('proposal-fee', 'Cargo fijo sobre el capital (%)', '5') : null;
  const term = lender ? field('proposal-term', 'Plazo esperado (meses)', config.default_term_months) : null;
  const check = el('button', 'button button-detail', 'Ver condiciones completas'); check.type = 'submit'; editor.append(check); $('#agreement-form').before(editor);
  editor.oninput = () => { previewVersion++; preview = null; draft = null; show('#agreement-form', false); show('#primary-action', false); };
  editor.onsubmit = event => {
    event.preventDefault();
    busy(check, async () => {
      const version = ++previewVersion;
      preview = null; draft = null; show('#agreement-form', false); show('#primary-action', false);
      const proposed = { [offer ? 'offer_id' : 'request_id']: id, amount: parseMoney(amount.value, { code: config.market_asset.asset_code, scale: config.market_asset.asset_scale }) };
      if (lender) {
        if (!/^\d{1,3}(?:[,.]\d{1,2})?$/.test(fee.value) || Number(fee.value.replace(',', '.')) > 100) throw new Error('El cargo debe estar entre 0 y 100 %, con hasta dos decimales.');
        if (!/^\d+$/.test(term.value) || Number(term.value) < 1 || Number(term.value) > 24) throw new Error('El plazo debe estar entre 1 y 24 meses.');
        proposed.fee_bps = Math.round(Number(fee.value.replace(',', '.')) * 100); proposed.term_months = Number(term.value);
      }
      const result = await api('previewLoan', { body: proposed });
      if (version !== previewVersion) return;
      preview = result; draft = proposed;
      terms(preview.terms, preview.example); show('#agreement-form'); show('#primary-action');
      text('#primary-action', lender ? 'Ofrecer préstamo' : 'Aceptar oferta'); $('#accept-terms').checked = false;
      show('#app-error', false);
    });
  };
}
