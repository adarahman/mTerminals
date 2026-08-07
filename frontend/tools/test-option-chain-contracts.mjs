import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const oc = read('OptionChain/option-chain.js');
const html = read('OptionChain/option-chain.html');
const sync = read('Dashboard/chain/chain-sync.js');
const spec = read('../docs/01_Product_Architecture/PDS-02_Option_Chain.md');

const checks = [
  ['shared feed state is broadcast', sync.includes("type:'oc-feed-state'") && sync.includes('feedState: (window.AppState && AppState.feedState)')],
  ['D-05 consumes shared feed state', oc.includes('sharedFeedState') && oc.includes('MARKET_CLOSED') && oc.includes('HOLIDAY') && oc.includes('PARTIAL')],
  ['dead iframe order path removed', !oc.includes('window._ocPlaceOrder') && !oc.includes('embedded in the dashboard\'s iframe')],
  ['Capital PCR preserves unavailable state', oc.includes('function capitalSummary') && oc.includes('return { pcr: "—", share: "—" }')],
  ['ATM/selected state appears in accessible row label', oc.includes('${r.isAtm ? ", ATM" : ""}') && oc.includes('${state.selectedStrike === r.strike ? ", selected" : ""}')],
  ['quick-order close has accessible label', oc.includes('aria-label="Close quick order"')],
  ['IV legend describes current IV', html.includes('<b>IV</b> implied volatility') && !html.includes('implied vol vs prior snapshot')],
  ['PDS permits paired bilateral stacks', spec.includes('paired bilateral PE/CE stacks')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} Option Chain contract checks passed.`);
