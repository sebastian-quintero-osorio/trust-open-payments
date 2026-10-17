const mode = document.body.dataset.mode;
const borrowing = mode === 'solicitar';
let creditLimit = Infinity;
let creditReadError = '';
if (borrowing) {
  try { creditLimit = TrustLoans.credit(formAccount); }
  catch (error) { creditLimit = 0; creditReadError = error.message; }
}
const form = document.querySelector('#loan-form');
const fieldNames = ['amount-min', 'amount-max', 'interest', 'repayment', 'term', 'notes'];
const fields = Object.fromEntries(fieldNames.map(name => [name, document.getElementById(name)]));
const touched = new Set();
const money = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 });
const decimals = new Intl.NumberFormat('es-CO', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const storageKey = ['esquina', 'aliado'].includes(formAccount) ? `trust.mockup.loan-draft.${mode}.v1` : `trust.mockup.loan-draft.${mode}.${formAccount}.v1`;
const draftStatus = document.querySelector('#draft-status');
const summaryError = document.querySelector('#form-errors');
const termLabel = value => `${value} ${value === 1 ? 'mes' : 'meses'}`;
const currency = value => `$${money.format(value)}`;

function parseAmount(raw) {
  const text = raw.trim();
  if (!/^(?:\d+|\d{1,3}(?:\.\d{3})+)$/.test(text)) return NaN;
  const value = Number(text.replaceAll('.', ''));
  return Number.isSafeInteger(value) ? value : NaN;
}
function parseDecimal(raw) {
  const text = raw.trim();
  if (!/^\d+(?:[.,]\d{1,2})?$/.test(text)) return NaN;
  const value = Number(text.replace(',', '.'));
  return Number.isFinite(value) && value <= Number.MAX_SAFE_INTEGER / 100 ? value : NaN;
}
function readValues() {
  const rawTerm = fields.term.value.trim();
  return {
    min: parseAmount(fields['amount-min'].value),
    max: parseAmount(fields['amount-max'].value),
    interest: parseDecimal(fields.interest.value),
    repayment: parseDecimal(fields.repayment.value),
    term: /^\d+$/.test(rawTerm) && Number.isSafeInteger(Number(rawTerm)) ? Number(rawTerm) : NaN,
    notes: fields.notes.value.trim()
  };
}
function issues(values) {
  const errors = {};
  if (creditReadError) return { 'amount-min': creditReadError };
  for (const [name, value] of [['amount-min', values.min], ['amount-max', values.max]]) {
    if (!Number.isFinite(value) || value <= 0) errors[name] = 'Escribe un monto mayor que cero, en pesos enteros.';
    else if (borrowing && value > creditLimit) errors[name] = `El monto no puede superar tu cupo de ${currency(creditLimit)}.`;
  }
  if (!errors['amount-min'] && !errors['amount-max'] && values.min > values.max) errors['amount-max'] = 'El monto máximo debe ser igual o mayor que el mínimo.';
  if (!Number.isFinite(values.interest) || values.interest < 0) errors.interest = 'Escribe una tasa de cero o más, con máximo dos decimales.';
  if (!Number.isFinite(values.repayment) || values.repayment <= 0 || values.repayment > 100) errors.repayment = 'Escribe un porcentaje mayor que 0 y menor o igual a 100.';
  if (!Number.isSafeInteger(values.term) || values.term <= 0) errors.term = 'Escribe un plazo en meses enteros, mayor que cero.';
  if (values.notes.length > 280) errors.notes = 'Usa un máximo de 280 caracteres.';
  return errors;
}
function updateErrors(all = false) {
  const errors = issues(readValues());
  fieldNames.forEach(name => {
    const show = (all || touched.has(name)) && Boolean(errors[name]);
    fields[name].setAttribute('aria-invalid', String(show));
    document.getElementById(`${name}-error`).textContent = show ? errors[name] : '';
  });
  if (all) {
    summaryError.hidden = Object.keys(errors).length === 0;
    summaryError.textContent = 'Revisa los campos indicados antes de continuar.';
  } else if (!Object.keys(errors).length) summaryError.hidden = true;
  return errors;
}
function rangeText(values, errors) {
  if (errors['amount-min'] || errors['amount-max']) return 'Completa un rango válido';
  if (values.min === values.max) return `${currency(values.min)} COP`;
  return `${currency(values.min)} a ${currency(values.max)} COP`;
}
function syncSummary() {
  const values = readValues();
  const errors = issues(values);
  document.querySelector('#summary-range').textContent = rangeText(values, errors);
  document.querySelector('#summary-interest').textContent = errors.interest ? 'Por definir' : `${borrowing ? 'Hasta ' : ''}${decimals.format(values.interest)} % E.A.`;
  document.querySelector('#summary-term').textContent = errors.term ? 'Por definir' : termLabel(values.term);
  document.querySelector('#summary-repayment').textContent = errors.repayment ? 'Por definir' : `${decimals.format(values.repayment)} %`;
  const validShare = !errors.repayment;
  document.querySelector('#repayment-example').hidden = !validShare;
  document.querySelector('#repayment-placeholder').hidden = validShare;
  if (validShare) {
    const payment = Math.round(100000 * values.repayment / 100);
    document.querySelector('#split-business').textContent = currency(100000 - payment);
    document.querySelector('#split-credit').textContent = currency(payment);
    document.querySelector('#business-bar').style.width = `${100 - values.repayment}%`;
  }
}
fieldNames.forEach(name => {
  fields[name].addEventListener('input', () => { syncSummary(); updateErrors(); draftStatus.textContent = ''; });
  fields[name].addEventListener('blur', () => {
    touched.add(name);
    if (name.startsWith('amount-')) {
      const value = parseAmount(fields[name].value);
      if (Number.isFinite(value)) fields[name].value = money.format(value);
    }
    updateErrors(); syncSummary();
  });
});

document.querySelector('#save-draft').addEventListener('click', () => {
  const draft = Object.fromEntries(fieldNames.map(name => [name, fields[name].value]));
  try {
    localStorage.setItem(storageKey, JSON.stringify(draft));
    draftStatus.textContent = 'Borrador guardado en este navegador. Aún no se ha publicado.';
  } catch { draftStatus.textContent = 'No fue posible guardar el borrador en este navegador. Los campos siguen disponibles en esta página.'; }
});
try {
  const saved = JSON.parse(localStorage.getItem(storageKey));
  if (saved && typeof saved === 'object' && fieldNames.every(name => typeof saved[name] === 'string' && saved[name].length <= 1000)) {
    fieldNames.forEach(name => { fields[name].value = saved[name]; });
    draftStatus.textContent = 'Borrador recuperado de este navegador.';
  }
} catch { /* La vista sigue disponible si el almacenamiento está bloqueado o dañado. */ }

const editor = document.querySelector('#edit-step');
const review = document.querySelector('#review-step');
const steps = [...document.querySelectorAll('.steps li')];
function setStep(index) {
  steps.forEach((step, position) => {
    if (position === index) step.setAttribute('aria-current', 'step');
    else step.removeAttribute('aria-current');
  });
  editor.hidden = index !== 0;
  review.hidden = index !== 1;
}
form.addEventListener('submit', event => {
  event.preventDefault();
  fieldNames.forEach(name => touched.add(name));
  const errors = updateErrors(true);
  if (Object.keys(errors).length) { fields[Object.keys(errors)[0]].focus(); return; }
  const values = readValues();
  document.querySelector('#review-range').textContent = rangeText(values, errors);
  document.querySelector('#review-interest').textContent = `${borrowing ? 'Hasta ' : ''}${decimals.format(values.interest)} % efectiva anual`;
  document.querySelector('#review-repayment').textContent = `${decimals.format(values.repayment)} % de cada venta`;
  document.querySelector('#review-term').textContent = termLabel(values.term);
  document.querySelector('#review-notes').textContent = values.notes || 'Sin información adicional.';
  setStep(1);
  document.querySelector('#review-title').focus();
});
document.querySelector('#edit-conditions').addEventListener('click', () => { setStep(0); fields['amount-min'].focus(); });
document.querySelector('#publish-demo').addEventListener('click', () => {
  document.querySelector('#loan-layout').hidden = true;
  document.querySelector('#success-panel').hidden = false;
  document.querySelector('#success-title').focus();
});
document.querySelector('#return-to-edit').addEventListener('click', () => {
  document.querySelector('#success-panel').hidden = true;
  document.querySelector('#loan-layout').hidden = false;
  setStep(0);
  fields['amount-min'].focus();
});
syncSummary();

if (creditReadError) { summaryError.hidden = false; summaryError.textContent = creditReadError; }
