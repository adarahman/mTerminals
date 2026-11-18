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
  window: {},
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

console.log('Commodity futures expiry selection: passed');
