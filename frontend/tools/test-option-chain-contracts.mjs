import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const sync = read('Dashboard/chain/chain-controls.js');
const template = read('Dashboard/chain/chain-template.js');
const dashboardHtml = read('Dashboard/DashboardPro.html');
const styles = read('styles/panels.css');
const paperStyles = read('styles/paper-trading.css');
const modalManager = read('Dashboard/modal-manager.js');
const build = read('build.mjs');
const spec = read('../docs/01_Product_Architecture/PDS-02_Option_Chain.md');
const rangeTabs = read('Dashboard/range-tabs.js');
const chainView = read('Dashboard/chain/chain-view.js');
const server = read('../src/server/cli_args.py');

const checks = [
  ['standalone Option Chain page is not built', !build.includes('OptionChain/option-chain.html')],
  ['strike navigation stays in dashboard', sync.includes('openStrikeDetailReportModal(n)') && !sync.includes('window.open(')],
  ['chain header no longer advertises a duplicate full page', !template.includes('Open full option chain')],
  ['snapshot header opens ledger in dashboard modal', template.includes('oc-ledger-table') && template.includes('openOptionChainModal(this)') && sync.includes("openOptionChainModal(button)")],
  ['LTP cells restore Buy Sell quick order', template.includes("ptOpenQuickOrder(event") && template.includes('Buy or sell')],
  ['LTP Buy Sell popup appears above modal', paperStyles.includes('z-index:calc(var(--z-modal) + 2)') && modalManager.includes("quickOrder.style.display = 'none'")],
  ['Greeks are a dedicated paired-column ledger view', dashboardHtml.includes('data-chain-view="greeks"') && dashboardHtml.includes('data-chain-view="all"') && template.includes('oc-metric greeks') && template.includes('Delta <small>PE / CE</small>') && template.includes('Gamma <small>PE / CE</small>') && !template.includes('oc-ledger-greeks') && sync.includes("['positioning', 'activity', 'greeks', 'all']")],
  ['dashboard CE/PE identity matches the ledger palette', styles.includes('.strike-link.ce{color:var(--ce);}') && styles.includes('.strike-link.pe{color:var(--pe);}') && styles.includes('.oi-snap-sides .ce{color:var(--ce);background:var(--ce-dim);}') && styles.includes('.oi-snap-sides .pe{color:var(--pe);background:var(--pe-dim);}')],
  ['dense ledger separates CE/PE identity from price direction', styles.includes('--oc-ce:var(--ce);--oc-pe:var(--pe)') && styles.includes('.oc-ledger-stack .ce{color:var(--oc-ce)}') && styles.includes('.oc-ledger-stack .pe{color:var(--oc-pe)}') && styles.includes('td.ltp.ce strong{color:var(--oc-ce)}') && styles.includes('td.ltp.pe strong{color:var(--oc-pe)}')],
  ['ledger signal styles match the generated signal classes', ['sig-strongbull','sig-strongbear','sig-mixed','sig-bull','sig-bear'].every(signalClass => styles.includes(`.oc-ledger-signal.${signalClass}`))],
  ['snapshot exposes CE and PE totals separately', ['CE OI','PE OI','CE ΔOI','PE ΔOI'].every(label => template.includes(label))],
  ['net OI metrics share their summary headers', template.includes('class="oi-snap-head-net"><small>Net OI') && template.includes('class="oi-snap-head-net"><small>Net ΔOI') && !template.includes('oi-snap-primary')],
  ['PCR change uses ratio precision instead of OI units', template.includes('const signedPcrDelta') && template.includes('signedPcrDelta(pcrShift)')],
  ['structure column uses shared per-strike classifier', template.includes('marketStructureLabels(chain') && template.includes('structureByStrike[Number(r.strike)]')],
  ['ATM ±15 is the real frontend and backend default', rangeTabs.includes('RANGE_TAB_DEFAULT = 15') && chainView.includes('this.chainRange = 15') && server.includes('default=15')],
  ['PDS permits paired bilateral stacks', spec.includes('paired bilateral PE/CE stacks')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} Option Chain contract checks passed.`);
