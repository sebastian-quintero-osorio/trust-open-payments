import { api } from '../lib/api.js';
import { $, $$, el, text, show, busy } from '../lib/ui.js';
import { money, parseMoney, percent } from '../lib/format.js';
import { home } from '../app.js';

export async function init(me) {
  const lender = me.role === 'lender';
  const [config, credit] = await Promise.all([api('getConfig'), lender ? null : api('getMyCreditProfile')]);
  const asset = { code: config.market_asset.asset_code, scale: config.market_asset.asset_scale };
  const form = $('#loan-form');
  let draft;
  const storageKey = `trust.draft.v1.${me.id}.${me.role}`;
  text('.fieldset-intro', lender ? 'Indica el mínimo y el máximo que quieres financiar.' : `Ambos montos deben estar dentro de tu cupo de ${money(credit.cupo_disponible, true)}.`);
  if (!lender) text('.context-amount', money(credit.cupo_disponible, true));
  text('label[for="interest"]', 'Cargo fijo sobre el capital');
  text('#interest-help', 'Cargo único acordado sobre el capital; no es una tasa efectiva anual.');
  text('#interest + span', '%');
  $('#interest').value = '5'; $('#term').value = config.default_term_months;
  text('#term-help', 'Plazo esperado que define el mínimo mensual de pago. Entre 1 y 24 meses.');
  $('#notes').closest('.field').remove();
  $('#review-notes').closest('div').remove();
  if (!lender) {
    $('#interest').closest('.field').hidden = true; $('#interest').disabled = true;
    $('#term').closest('.field').hidden = true; $('#term').disabled = true;
    for (const selector of ['#summary-interest', '#review-interest', '#summary-term', '#review-term']) $(selector).closest('div').hidden = true;
  } else {
    for (const selector of ['#summary-interest', '#review-interest']) $(selector).previousElementSibling.textContent = 'Cargo fijo sobre el capital';
  }
  const old = $('#repayment'), select = el('select');
  select.id = old.id; select.name = old.name; select.setAttribute('aria-describedby', 'repayment-help repayment-error');
  for (const bps of config.pct_options_bps) { const option = el('option', '', percent(bps)); option.value = bps; select.append(option); }
  old.replaceWith(select); select.nextElementSibling?.remove();
  const ids = ['amount-min', 'amount-max', 'repayment', ...(lender ? ['interest', 'term'] : [])];
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    if (saved) for (const id of ids) if (typeof saved[id] === 'string') $(`#${id}`).value = saved[id];
  } catch { /* A corrupt local draft must not prevent creating a request. */ }
  function read() {
    const errors = {};
    let min, max;
    for (const [id, assign] of [['amount-min', value => min = value], ['amount-max', value => max = value]]) {
      try {
        const value = parseMoney($(`#${id}`).value, asset); assign(value);
        if (BigInt(value.value) < BigInt(config.min_loan_amount.value)) errors[id] = `El monto mínimo es ${money(config.min_loan_amount, true)}.`;
        else if (BigInt(value.value) % BigInt(config.amount_step.value) !== 0n) errors[id] = `Usa múltiplos de ${money(config.amount_step, true)}.`;
        else if (!lender && BigInt(value.value) > BigInt(credit.cupo_disponible.value)) errors[id] = 'El monto supera tu cupo disponible.';
      } catch (error) { errors[id] = error.message; }
    }
    if (min && max && BigInt(min.value) > BigInt(max.value)) errors['amount-max'] = 'El máximo debe ser igual o mayor al mínimo.';
    const pct = Number(select.value);
    if (!config.pct_options_bps.includes(pct)) errors.repayment = 'Selecciona uno de los porcentajes disponibles.';
    let fee, term;
    if (lender) {
      const raw = $('#interest').value.trim();
      if (!/^\d{1,3}(?:[,.]\d{1,2})?$/.test(raw)) errors.interest = 'Escribe un porcentaje entre 0 y 100, con hasta 2 decimales.';
      fee = Math.round(Number(raw.replace(',', '.')) * 100);
      if (!Number.isInteger(fee) || fee < 0 || fee > 10000) errors.interest = 'El cargo debe estar entre 0 y 100 %.';
      term = Number($('#term').value);
      if (!/^\d+$/.test($('#term').value) || term < 1 || term > 24) errors.term = 'El plazo debe ser un entero entre 1 y 24 meses.';
    }
    if (!lender && !credit.eligible) errors['amount-min'] = credit.reasons.join(' ') || 'Todavía no tienes cupo para solicitar crédito.';
    return { errors, body: { min_amount: min, max_amount: max, pct_bps: pct, ...(lender ? { fee_bps: fee, term_months: term } : {}) } };
  }
  function summary(displayErrors = false) {
    const { errors, body } = read();
    if (displayErrors) {
      for (const id of ids) { text(`#${id}-error`, errors[id] || ''); $(`#${id}`).setAttribute('aria-invalid', String(Boolean(errors[id]))); }
      text('#form-errors', Object.values(errors).join(' ')); show('#form-errors', Boolean(Object.keys(errors).length));
    }
    const range = errors['amount-min'] || errors['amount-max'] ? 'Por definir' : `${money(body.min_amount)} a ${money(body.max_amount)} COP`;
    for (const prefix of ['summary', 'review']) {
      text(`#${prefix}-range`, range); text(`#${prefix}-interest`, errors.interest ? 'Por definir' : percent(body.fee_bps));
      text(`#${prefix}-repayment`, percent(body.pct_bps)); text(`#${prefix}-term`, `${body.term_months} meses`);
    }
    const example = config.example_splits.find(e => e.pct_bps === body.pct_bps);
    show('#repayment-example', Boolean(example));
    if (example) {
      text('#repayment-example > p', `Ejemplo con una venta de ${money(config.example_sale, true)}`);
      text('#split-business', money(example.merchant)); text('#split-credit', money(example.abono));
      $('#business-bar').style.width = `${100 - body.pct_bps / 100}%`;
    }
    return { errors, body };
  }
  form.addEventListener('input', () => { summary(); draft = null; });
  form.addEventListener('change', () => { summary(); draft = null; });
  function step(review) {
    show('#edit-step', !review); show('#review-step', review);
    $$('.steps li').forEach((li, i) => { if (i === Number(review)) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current'); });
  }
  form.onsubmit = event => {
    event.preventDefault(); const { errors, body } = summary(true);
    if (Object.keys(errors).length) { $(`#${Object.keys(errors)[0]}`).focus(); return; }
    draft = body; step(true); $('#review-title').focus();
  };
  $('#edit-conditions').onclick = () => { step(false); $('#amount-min').focus(); };
  $('#save-draft').onclick = () => {
    try { localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(ids.map(id => [id, $(`#${id}`).value])))); text('#draft-status', 'Borrador guardado en este navegador. Aún no se ha publicado.'); }
    catch { text('#draft-status', 'No se pudo guardar el borrador. Los campos siguen disponibles.'); }
  };
  $('#publish-demo').onclick = () => busy($('#publish-demo'), async () => {
    if (!draft) throw new Error('Vuelve a revisar las condiciones.');
    await api(lender ? 'createLoanOffer' : 'createLoanRequest', { body: draft });
    try { localStorage.removeItem(storageKey); } catch { /* Publication succeeded independently of local storage. */ }
    location.assign(home(me.role) + '#historial');
  });
  text('.review-notice', 'Publicar permite buscar una contraparte. El desembolso requiere la aceptación del comercio y la autorización del prestamista en su wallet.');
  summary();
}
