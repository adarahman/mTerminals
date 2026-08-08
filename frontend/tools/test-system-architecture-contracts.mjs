import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const helpers = read('Dashboard/chain/chain-helpers.js');
const renderer = read('Dashboard/chain/chain-renderer.js');
const store = read('shared/stores/market-store.js');
const service = read('shared/services/data-service.js');
const server = read('../ws_server_live.py');
const modal = read('Dashboard/modal-manager.js');
const chainView = read('Dashboard/chain/chain-view.js');

const checks = [
  ['ticker rows have stable keys', helpers.includes('data-index-symbol="VIX"') && helpers.includes('data-index-symbol="${backendSymbol}"')],
  ['routine ticker updates patch fields', helpers.includes('function patchIndexTicker(d)') && helpers.includes("value.textContent = fmtI(idx['Last Price'])")],
  ['ticker rebuild is structural only', helpers.includes("expected.join('|') !== existing.join('|')")],
  ['live renderer avoids ticker outerHTML replacement', renderer.includes('patchIndexTicker(d)') && !renderer.includes('tickerEl.outerHTML = html')],
  ['full snapshots establish a wire baseline', server.includes('"version": _BASELINE_ID') && store.includes('this.baselineVersion = msg.version || null')],
  ['incompatible deltas are rejected', store.includes('msg.baseVersion !== this.baselineVersion') && store.includes("this.emit('baselineMismatch'")],
  ['baseline mismatch requests coherent recovery', service.includes("this.store.on('baselineMismatch'") && service.includes('this.wsManager.connect(undefined, true)')],
  ['market events carry metadata rather than snapshots', store.includes('messageType: msg && msg.type') && !store.includes("emit('market:update', this.state)")],
  ['semantic navigation events are published', chainView.includes("emit('range:change'") && renderer.includes("emit('expiry:change'") && modal.includes("emit('strike:select'")],
  ['modal lifecycle is semantic and restores focus', modal.includes("emit('modal:open'") && modal.includes("emit('modal:close'") && modal.includes('requestAnimationFrame(() => invoker.focus())')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} system architecture checks passed.`);
