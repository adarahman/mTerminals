import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(
  new URL('../Dashboard/commodity-prices.js', import.meta.url),
  'utf8',
);
const expirySelect = {
  disabled: true,
  options: [],
  replaceChildren(...options) {
    this.options = options;
  },
};
const fetchedSlots = [];
const quoteBySlot = {
  NEAR: '30-Jun-2026',
  NEXT: '31-Jul-2026',
  FAR: '31-Aug-2026',
};
const context = {
  window: {dashboardInstrumentMode: 'commodity'},
  document: {
    getElementById(id) {
      return id === 'commodity-futures-expiry' ? expirySelect : null;
    },
  },
  Option: class {
    constructor(text, value) {
      this.textContent = text;
      this.value = value;
      this.selected = false;
    }
  },
  URLSearchParams,
  fetch: async (url) => {
    const which = new URL(url, 'http://localhost').searchParams.get('which');
    fetchedSlots.push(which);
    return {
      status: 200,
      ok: true,
      json: async () => ({quote: {Expiry: quoteBySlot[which]}}),
    };
  },
};
vm.runInNewContext(`${source}\nglobalThis.CommodityPricesView = CommodityPricesView;`, context);

const view = new context.CommodityPricesView();
view.symbol = 'CRUDEOIL';
const refreshes = [];
view._refreshQuote = (symbol) => {
  refreshes.push({symbol, expiry: view.futureExpiry, which: view._futureSlot()});
};

await view._refreshFutureExpiries('CRUDEOIL');

assert.deepEqual(fetchedSlots.sort(), ['FAR', 'NEAR', 'NEXT']);
assert.deepEqual(
  expirySelect.options.map((option) => option.value),
  ['30-Jun-2026', '31-Jul-2026', '31-Aug-2026'],
);
assert.equal(expirySelect.disabled, false);
assert.equal(view.futureExpiry, '30-Jun-2026');

view.selectFutureExpiry('31-Jul-2026');
assert.equal(view.futureExpiry, '31-Jul-2026');
assert.deepEqual(refreshes.at(-1), {
  symbol: 'CRUDEOIL',
  expiry: '31-Jul-2026',
  which: 'NEXT',
});
view._refreshQuote = context.CommodityPricesView.prototype._refreshQuote.bind(view);

const equityData = {symbol: 'NIFTY', spot: 24800};
context._data = equityData;
context.window.dashboardInstrumentMode = 'equity';
view.applyLiveQuote({
  symbol: 'CRUDEOIL',
  quote: {LTP: 6000, Expiry: '31-Jul-2026'},
});
view._syncSharedQuoteContext();
view._renderSharedOptionChain({chain: {rows: []}});
view._toSharedDashboardData();
assert.equal(context._data, equityData);
assert.equal(view.latestQuote, null);

view.symbol = 'CRUDEOIL';
view.latestQuote = null;
view.latestChainPayload = null;
view.applyLiveQuote({
  symbol: 'SILVERM',
  quote: {
    Underlying: 'SILVERM',
    Contract: 'SILVERM30NOV26FUT',
    LTP: 146692,
  },
});
assert.equal(view.latestQuote, null);
const isolatedData = view._toSharedDashboardData();
assert.equal(isolatedData.symbol, 'CRUDEOIL');
assert.equal(isolatedData.spot, 0);
view.latestQuote = {
  Underlying: 'SILVERM',
  Contract: 'SILVERM30NOV26FUT',
  LTP: 146692,
};
view.latestChainPayload = {
  symbol: 'SILVERM',
  expiry: '30NOV2026',
  chain: {symbol: 'SILVERM', rows: [{strike: 146000, type: 'CE', ltp: 100}]},
};
const staleData = view._toSharedDashboardData();
assert.equal(staleData.spot, 0);
assert.equal(staleData.chain.length, 0);
view.symbol = 'SILVERM';
view.latestChainPayload = {
  symbol: 'SILVERM',
  expiry: '30NOV2026',
  chain: {
    spot: 146000,
    atm_strike: 146000,
    atm_iv: 55.46,
    rows: [
      {strike: 146000, type: 'CE', ltp: 300, net_change: 14.5, pct_change: 0.4, volume: 12, oi: 100},
      {strike: 146000, type: 'PE', ltp: 280, net_change: -8.25, pct_change: -0.3, volume: 15, oi: 120},
    ],
    greeks: [],
  },
};
const commodityData = view._toSharedDashboardData();
assert.equal(commodityData.atmIV, 55.46);
assert.equal(commodityData.risk.tradeGrade, 'Not rated');
assert.equal(commodityData.risk.ivRegime, '55.46%');
assert.equal(commodityData.decision.verdicts.atmIV, '55.46%');
assert.equal(commodityData.chain[0].ceChg, 14.5);
assert.equal(commodityData.chain[0].ceChgPct, 0.4);
assert.equal(commodityData.chain[0].peChg, -8.25);
assert.equal(commodityData.chain[0].peChgPct, -0.3);
view.symbol = 'CRUDEOIL';
view.latestQuote = null;
view.latestChainPayload = null;

let resolveQuoteResponse;
context.fetch = () => new Promise((resolve) => {
  resolveQuoteResponse = resolve;
});
context.window.dashboardInstrumentMode = 'commodity';
const quoteRequest = view._refreshQuote('CRUDEOIL');
context.window.dashboardInstrumentMode = 'equity';
resolveQuoteResponse({
  status: 200,
  ok: true,
  json: async () => ({quote: {LTP: 6000, Expiry: '31-Jul-2026'}}),
});
await quoteRequest;
assert.equal(context._data, equityData);
assert.equal(view.latestQuote, null);

let resolveChainResponse;
context.fetch = () => new Promise((resolve) => {
  resolveChainResponse = resolve;
});
context.window.dashboardInstrumentMode = 'commodity';
const chainRequest = view._refreshOptionChain('CRUDEOIL');
context.window.dashboardInstrumentMode = 'equity';
resolveChainResponse({
  status: 200,
  ok: true,
  json: async () => ({
    expiry: '31-Jul-2026',
    chain: {rows: []},
    analysis: {},
  }),
});
await chainRequest;
assert.equal(context._data, equityData);
assert.equal(view.latestChainPayload, null);

console.log('Commodity futures expiry selection: passed');
