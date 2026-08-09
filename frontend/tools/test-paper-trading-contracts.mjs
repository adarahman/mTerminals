import assert from 'node:assert/strict';
import fs from 'node:fs';

const shared = fs.readFileSync(new URL('../Dashboard/paper-trading-shared.js', import.meta.url), 'utf8');
const tracker = fs.readFileSync(new URL('../Dashboard/portfolio-tracker.js', import.meta.url), 'utf8');

assert.match(shared, /client_order_id/, 'paper submissions need durable client identity');
assert.match(shared, /payload\.live \? 'l' : 'p'/,
  'every live and paper submission must receive a bounded mode-specific identity');
assert.match(shared, /status:'SUBMITTED'/, 'submitted lifecycle state must be explicit');
assert.match(shared, /portfolio\.funds/, 'paper funds must consume the backend account snapshot');
assert.match(tracker, /SIMULATION ONLY/, 'paper fill assumptions must be visible');
assert.match(tracker, /SIM FILLED/, 'paper fills must not resemble exchange confirmations');
assert.match(tracker, /SIM REJECTED/, 'paper rejection lifecycle must be explicit');
assert.match(tracker, /No added slippage or artificial delay/, 'simulation assumptions must document slippage and delay');
const orderEntry = fs.readFileSync(new URL('../Dashboard/order-entry.js', import.meta.url), 'utf8');
assert.doesNotMatch(orderEntry, /sendWsMessage\('place_basket_order'/,
  'UI must not expose an unhandled basket submission path');
assert.doesNotMatch(orderEntry, /data-value="(?:SL|SL-M|TSL|GTT)"/,
  'UI must expose only paper order types implemented by the backend');
assert.match(orderEntry, /Bulk live strategy blocked/,
  'multi-leg live strategies must fail closed until basket confirmation is atomic');
assert.match(tracker, /if\(_ptLiveMode\).*Live square-off blocked/s,
  'paper positions must never generate real square-off orders');
assert.match(tracker, /LIVE FUNDS · PAPER POSITIONS/,
  'portfolio must identify its mixed live-funds and paper-position sources');
assert.match(shared, /Estimated value:/,
  'live confirmation must show the estimated order value');
assert.match(shared, /totalUnits/,
  'live confirmation must show lot-adjusted total units');

console.log('Paper trading contracts: 15/15 passed');
