import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const helpers = read('Dashboard/chain/chain-helpers.js');
const marketContext = read('Dashboard/chain/market-context.js');
const renderer = [
  read('Dashboard/chain/chain-dense-renderer.js'),
  read('Dashboard/chain/chain-dashboard-renderer.js'),
  read('Dashboard/chain/chain-analytics-renderer.js'),
].join('\n');
const store = read('shared/stores/market-store.js');
const service = read('shared/services/data-service.js');
const server = read('../src/application/market_service.py');
const modal = read('Dashboard/modal-manager.js');
const chainView = read('Dashboard/chain/chain-view.js');
const storeContext = vm.createContext({ window: {} });
vm.runInContext(`${store}\nthis.MarketStore = MarketStore;`, storeContext);
const deltaStore = new storeContext.MarketStore();
deltaStore.ingest({
  type: 'full', version: 'NIFTY:EXP:1',
  payload: { chain: [{ strike: 24000, ceLTP: 100, ceOI: 500, temporary: true }] },
});
deltaStore.ingest({
  type: 'delta', baseVersion: 'NIFTY:EXP:1',
  payload: { chain: { _keyed: true, _key_field: 'strike', changed: [
    { strike: 24000, ceLTP: 101.5, _removed: ['temporary'] },
  ] } },
});
const patchedRow = deltaStore.state.chain[0];

const checks = [
  ['ticker rows have stable keys', marketContext.includes('data-index-symbol="VIX"') && marketContext.includes('data-index-symbol="${backendSymbol}"')],
  ['symbol switch encodes once via URLSearchParams', marketContext.includes("params.set('symbol', sym)") && !marketContext.includes("params.set('symbol', encodeURIComponent(")],
  ['routine ticker updates patch fields', marketContext.includes('function patchIndexTicker(d)') && marketContext.includes("value.textContent = fmtI(idx['Last Price'])")],
  ['ticker rebuild is structural only', marketContext.includes("expected.join('|') !== existing.join('|')")],
  ['live renderer avoids ticker outerHTML replacement', renderer.includes('patchIndexTicker(d)') && !renderer.includes('tickerEl.outerHTML = html')],
  ['full snapshots establish a wire baseline', server.includes('"type": "full"') && store.includes('this.baselineVersion = msg.version || null')],
  ['incompatible deltas are rejected', store.includes('msg.baseVersion !== this.baselineVersion') && store.includes("this.emit('baselineMismatch'")],
  ['baseline mismatch requests coherent recovery', service.includes("this.store.on('baselineMismatch'") && service.includes('this.wsManager.connect(undefined, true)')],
  ['market events carry metadata rather than snapshots', store.includes('messageType: msg && msg.type') && !store.includes("emit('market:update', this.state)")],
  ['semantic navigation events are published', chainView.includes("emit('range:change'") && renderer.includes("emit('expiry:change'") && modal.includes("emit('strike:select'")],
  ['modal lifecycle is semantic and restores focus', modal.includes("emit('modal:open'") && modal.includes("emit('modal:close'") && modal.includes('requestAnimationFrame(() => invoker.focus())')],
  ['partial keyed deltas patch and remove row fields', patchedRow.ceLTP === 101.5 && patchedRow.ceOI === 500 && !('temporary' in patchedRow) && !('_removed' in patchedRow)],
  ['auxiliary store changes bypass full market rendering', store.includes("messageType: msg && msg.type") && service.includes("messageType === 'portfolio'") && service.includes("messageType === 'algoStatus'") && service.includes("messageType === 'indexQuotes'") && service.includes('window.patchIndexTicker(AppState.wsState)')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} system architecture checks passed.`);
