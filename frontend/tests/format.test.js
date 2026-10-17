import test from 'node:test';
import assert from 'node:assert/strict';
import { money, parseMoney, loanState, sumMoney } from '../src/lib/format.js';
import { nextPage } from '../src/lib/navigation.js';

test('money preserves cents and values larger than Number.MAX_SAFE_INTEGER', () => {
  assert.equal(money({ value: '4800000', asset_code: 'COP', asset_scale: 2 }), '$48.000');
  assert.equal(money({ value: '4800050', asset_code: 'COP', asset_scale: 2 }), '$48.000,5');
  assert.equal(money({ value: '900719925474099301', asset_code: 'COP', asset_scale: 2 }), '$9.007.199.254.740.993,01');
  assert.equal(money({ value: '1250', asset_code: 'USD', asset_scale: 2 }), '12,5 USD');
});
test('input uses exact integer minor units and rejects malformed grouping', () => {
  assert.equal(parseMoney('5.000.000').value, '500000000');
  assert.equal(parseMoney('0,01').value, '1');
  for (const invalid of ['1.2.3', '-100', '1e9', '1,001', 'Infinity', '']) assert.throws(() => parseMoney(invalid));
});
test('payment states require confirmed active or paid loans', () => {
  assert.equal(loanState({ status: 'pending_disbursement', is_late: false }), 'Por desembolsar');
  assert.equal(loanState({ status: 'active', is_late: true }), 'En retraso');
  assert.equal(loanState({ status: 'paid', is_late: false }), 'Pagado');
});
test('financial totals keep exact units and reject incompatible assets', () => {
  const asset = { asset_code: 'COP', asset_scale: 2 };
  assert.deepEqual(sumMoney([{ ...asset, value: '900719925474099301' }, { ...asset, value: '99' }], asset), { ...asset, value: '900719925474099400' });
  assert.throws(() => sumMoney([{ ...asset, value: '1', asset_code: 'USD' }], asset));
  assert.throws(() => sumMoney([{ ...asset, value: '1', asset_scale: 0 }], asset));
});
test('post-login destinations remain on local product routes', () => {
  globalThis.location = { origin: 'https://trust.example.test' };
  const fallback = '/portal-prestamos/index.html';
  for (const target of ['https://attacker.test', '//attacker.test', '/\\attacker.test', '/autenticacion/index.html', '/op/../../external', '/%2f%2fattacker.test']) assert.equal(nextPage(target, fallback), fallback);
  assert.equal(nextPage('/op/return?loan_id=123&op_status=completed', fallback), '/op/return?loan_id=123&op_status=completed');
  delete globalThis.location;
});
