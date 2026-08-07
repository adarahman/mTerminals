import assert from 'node:assert/strict';
import fs from 'node:fs';

const shared = fs.readFileSync(new URL('../Dashboard/paper-trading-shared.js', import.meta.url), 'utf8');
const tracker = fs.readFileSync(new URL('../Dashboard/portfolio-tracker.js', import.meta.url), 'utf8');

assert.match(shared, /client_order_id/, 'paper submissions need durable client identity');
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

console.log('Paper trading contracts: 9/9 passed');
