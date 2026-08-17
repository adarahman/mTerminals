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
const server = read('../ws_server_live.py');

const checks = [
  ['standalone Option Chain page is not built', !build.includes('OptionChain/option-chain.html')],
  ['strike navigation stays in dashboard', sync.includes('openStrikeDetailReportModal(n)') && !sync.includes('window.open(')],
  ['chain header no longer advertises a duplicate full page', !template.includes('Open full option chain')],
  ['snapshot header opens ledger in dashboard modal', template.includes('oc-ledger-table') && template.includes('openOptionChainModal(this)') && sync.includes("openOptionChainModal(button)")],
  ['LTP cells restore Buy Sell quick order', template.includes("ptOpenQuickOrder(event") && template.includes('Buy or sell')],
  ['LTP Buy Sell popup appears above modal', paperStyles.includes('z-index:calc(var(--z-modal) + 2)') && modalManager.includes("quickOrder.style.display = 'none'")],
  ['Greeks toggle sits in modal header and restores per-strike rows', dashboardHtml.includes('id="option-chain-greeks-toggle"') && dashboardHtml.includes('toggleOptionChainGreeks(this)') && !template.includes('Live strike ledger') && template.includes('oc-ledger-greeks') && sync.includes("row.hidden = !visible")],
  ['CE uses red and PE uses green', styles.includes('.strike-link.ce{color:var(--neg);}') && styles.includes('.strike-link.pe{color:var(--pos);}')],
  ['dense ledger keeps CE red and PE green', styles.includes('.oc-ledger-stack .ce{color:var(--neg)}') && styles.includes('.oc-ledger-stack .pe{color:var(--pos)}') && styles.includes('td.ltp.ce strong{color:var(--neg)}') && styles.includes('td.ltp.pe strong{color:var(--pos)}')],
  ['snapshot exposes CE and PE totals separately', ['CE OI','PE OI','CE ΔOI','PE ΔOI'].every(label => template.includes(label))],
  ['structure column uses shared per-strike classifier', template.includes('marketStructureLabels(chain') && template.includes('structureByStrike[Number(r.strike)]')],
  ['ATM ±15 is the real frontend and backend default', rangeTabs.includes('RANGE_TAB_DEFAULT = 15') && chainView.includes('this.chainRange = 15') && server.includes('(15 if USE_SMARTAPI else 50)')],
  ['PDS permits paired bilateral stacks', spec.includes('paired bilateral PE/CE stacks')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} Option Chain contract checks passed.`);
