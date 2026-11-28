import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(
  new URL('../Dashboard/currency-rates.js', import.meta.url),
  'utf8',
);
const topBar = fs.readFileSync(
  new URL('../Dashboard/chain/chain-template.js', import.meta.url),
  'utf8',
);
const topBarRenderer = fs.readFileSync(
  new URL('../Dashboard/chain/chain-dashboard-renderer.js', import.meta.url),
  'utf8',
);
const requests = [];
const api = {currencyQuote: '/api/currency/quote', currencyOptionChain: '/api/currency/option-chain'};
const context = {
  Config: {api},
  URLSearchParams,
  localStorage: {setItem() {}},
  console: {warn() {}},
  fetch: async (url) => {
    requests.push(String(url));
    return {ok: false, status: 501, json: async () => ({error: 'CDS adapter unavailable'})};
  },
  window: {Config: {api}, setInterval: () => 1, clearInterval() {}},
};
vm.runInNewContext(source, context);

const view = context.window.currencyRates;
view.latestQuote = {LTP: 83.421, Change: 0.036, PctChange: 0.04, LotSize: 1000};
view.futuresExpiry = 'NEXT';
view.quoteError = 'CDS adapter unavailable';
const projected = view.projectPayload({});
assert.equal(projected.spot, 83.421);
assert.equal(projected.spotChange, 0.036);
assert.equal(projected.futuresExpiry, 'NEXT');
assert.equal(projected.quoteError, 'CDS adapter unavailable');
assert.match(topBar, /isCurrency \? fmtN\(d\.spotChange, 4\)/);
assert.match(topBarRenderer, /isCurrency \? fmtN\(d\.spotChange, 4\)/);

view.quoteError = '';
view.selectFutureExpiry('FAR');
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(view.futuresExpiry, 'FAR');
assert(requests.some((url) => url.includes('which=FAR') && url.includes('/quote')));
assert(requests.some((url) => url.includes('which=FAR') && url.includes('/option-chain')));
assert.equal(view.quoteError, 'CDS adapter unavailable');

console.log('Currency rates contracts: 10/10 passed');
