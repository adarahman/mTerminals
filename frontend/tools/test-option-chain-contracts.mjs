import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const sync = read('Dashboard/chain/chain-controls.js');
const template = read('Dashboard/chain/chain-template.js');
const styles = read('styles/panels.css');
const build = read('build.mjs');
const spec = read('../docs/01_Product_Architecture/PDS-02_Option_Chain.md');

const checks = [
  ['standalone Option Chain page is not built', !build.includes('OptionChain/option-chain.html')],
  ['strike navigation stays in dashboard', sync.includes('openStrikeDetailReportModal(n)') && !sync.includes('window.open(')],
  ['chain header no longer advertises a duplicate full page', !template.includes('Open full option chain')],
  ['snapshot header opens earlier ledger-style table', template.includes('oc-ledger-table') && template.includes('Premium ₹') && template.includes('Footprint') && sync.includes('table.hidden = !nextExpanded')],
  ['LTP cells restore Buy Sell quick order', template.includes("ptOpenQuickOrder(event") && template.includes('Buy or sell')],
  ['Greeks toggle restores per-strike rows', template.includes('toggleOptionChainGreeks(this)') && template.includes('oc-ledger-greeks') && sync.includes("row.hidden = !visible")],
  ['CE uses red and PE uses green', styles.includes('.strike-link.ce{color:var(--neg);}') && styles.includes('.strike-link.pe{color:var(--pos);}')],
  ['snapshot exposes CE and PE totals separately', ['CE OI','PE OI','CE ΔOI','PE ΔOI'].every(label => template.includes(label))],
  ['PDS permits paired bilateral stacks', spec.includes('paired bilateral PE/CE stacks')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} Option Chain contract checks passed.`);
