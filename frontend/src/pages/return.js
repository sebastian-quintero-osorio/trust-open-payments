import { api } from '../lib/api.js';
import { $, el, text, polling, errorMessage } from '../lib/ui.js';
import { loanState } from '../lib/format.js';

function loanId() {
  const id = new URLSearchParams(location.search).get('loan_id');
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id || '')) throw new Error('El enlace de retorno no contiene un préstamo válido.');
  return id;
}

export function mobileReturn() {
  const id = loanId(), target = `trust://loans/${id}`;
  text('#return-status', 'Vuelve a la app de Trust para consultar el estado confirmado del préstamo.');
  const link = el('a', 'button button-primary', 'Abrir en Trust'); link.href = target; $('main').append(link);
  // The native app owns the token; a wallet browser need not have a web session.
  location.assign(target);
}

export async function init() {
  const query = new URLSearchParams(location.search), id = loanId();
  let stop = () => {}, attempts = 0, settled = false;
  const error = el('p', 'app-error'); error.id = 'return-error'; error.hidden = true; error.setAttribute('role', 'alert'); $('main').append(error);
  const link = el('a', 'button button-primary', 'Ver préstamo'); link.href = `/detalle-prestamo/oferta.html?loan_id=${encodeURIComponent(id)}`; $('main').append(link);
  async function check() {
    // op_status is deliberately not used as proof of payment.
    const loan = await api('getLoan', { params: { loan_id: id } });
    error.hidden = true;
    text('#return-status', loan.status === 'disbursing' ? 'Estamos confirmando el desembolso con tu wallet…' : loanState(loan));
    if (loan.status !== 'disbursing') {
      settled = true;
      stop();
      if (query.get('client') === 'mobile') location.assign(`trust://loans/${encodeURIComponent(id)}`);
    } else if (++attempts >= 60) {
      settled = true;
      stop(); text('#return-status', 'La confirmación sigue pendiente. Puedes consultar el préstamo para revisar su estado.');
    }
  }
  await check();
  if (!settled) stop = polling(check, 2000, { onError: failure => errorMessage(failure, '#return-error') });
}
