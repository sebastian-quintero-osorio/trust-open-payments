export function money(amount, withCode = false) {
  if (!amount || !/^\d+$/.test(amount.value)) return '—';
  const scale = amount.asset_scale;
  const digits = amount.value.padStart(scale + 1, '0');
  const whole = BigInt(scale ? digits.slice(0, -scale) : digits).toLocaleString('es-CO');
  const fraction = scale ? digits.slice(-scale).replace(/0+$/, '') : '';
  return `${amount.asset_code === 'COP' ? '$' : ''}${whole}${fraction ? ',' + fraction : ''}${withCode || amount.asset_code !== 'COP' ? ' ' + amount.asset_code : ''}`;
}
export function parseMoney(value, asset = { code: 'COP', scale: 2 }) {
  const text = value.trim();
  if (!/^(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$/.test(text)) throw new Error('Escribe un monto válido, por ejemplo 1.500.000.');
  const [whole, fraction = ''] = text.replaceAll('.', '').split(',');
  if (fraction.length > asset.scale) throw new Error(`Usa hasta ${asset.scale} decimales.`);
  return { value: BigInt(whole + fraction.padEnd(asset.scale, '0')).toString(), asset_code: asset.code, asset_scale: asset.scale };
}
export const percent = bps => bps == null ? '—' : `${new Intl.NumberFormat('es-CO', { maximumFractionDigits: 2 }).format(bps / 100)} %`;
export const date = value => value ? new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium', timeZone: 'America/Bogota' }).format(new Date(value)) : '—';
export const name = ref => ref?.business_name || ref?.public_name || '—';
export const initials = value => value.split(/\s+/).slice(0, 2).map(x => x[0]).join('').toUpperCase();
export const searchText = value => value.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase('es').trim();
export const deliveryState = status => ({ pending: 'Pendiente', processing: 'En proceso', in_progress: 'En proceso', completed: 'Entregado', failed: 'Falló la entrega', failed_final: 'Falló la entrega', held: 'Retenido', skipped: 'Sin transferencia', none: 'Sin entrega' })[status] || status;
export const payoutLeg = leg => ({ merchant: 'Comercio', lender: 'Prestamista', trust: 'Trust' })[leg] || leg;
export function sumMoney(amounts, asset) {
  let total = 0n;
  for (const amount of amounts) {
    if (amount.asset_code !== asset.asset_code || amount.asset_scale !== asset.asset_scale) throw new Error('No se pueden sumar importes de monedas o escalas diferentes.');
    total += BigInt(amount.value);
  }
  return { value: total.toString(), asset_code: asset.asset_code, asset_scale: asset.asset_scale };
}
export const loanTone = loan => loan.status === 'paid' ? 'green' : loan.status === 'active' ? loan.is_late ? 'amber' : 'green' : loan.status === 'pending_disbursement' ? 'lavender' : 'neutral';
export function loanState(loan) {
  if (loan.status === 'active') return loan.is_late ? 'En retraso' : 'Al día';
  return ({ paid: 'Pagado', pending_acceptance: 'Por aceptar', pending_disbursement: 'Por desembolsar', disbursing: 'Autorizando desembolso', rejected: 'Rechazado', cancelled: 'Cancelado', expired: 'Vencido', open: 'Disponible', matched: 'Acordado', taken: 'Acordado' })[loan.status] || loan.status;
}
