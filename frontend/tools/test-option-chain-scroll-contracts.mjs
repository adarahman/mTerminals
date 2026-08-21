import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const renderer = fs.readFileSync(
  path.join(root, 'Dashboard/chain/chain-analytics-renderer.js'), 'utf8');
const controls = fs.readFileSync(
  path.join(root, 'Dashboard/chain/chain-controls.js'), 'utf8');
const modalManager = fs.readFileSync(
  path.join(root, 'Dashboard/modal-manager.js'), 'utf8');
const styles = fs.readFileSync(path.join(root, 'styles/panels.css'), 'utf8');

const checks = [
  ['compact chain captures its scroll offset', renderer.includes('previousChainScrollTop = chainWrap ? chainWrap.scrollTop : null')],
  ['compact chain only recenters when viewport identity changes', renderer.includes('if(chainViewportChanged) _centerChainOnATM=true')],
  ['expanded ledger preserves its browsing position', renderer.includes("card.querySelector('.oc-native-scroll')") && renderer.includes('scroll.scrollTop = position.top')],
  ['expanded ledger defers replacement during active scrolling', renderer.includes('_chainLedgerScrollState.activeUntil') && renderer.includes('shouldSkip: (card)')],
  ['expanded ledger restores again after browser layout', renderer.includes('requestAnimationFrame(restore)')],
  ['expanded ledger keeps its physical scroll container across ticks', renderer.includes('Never replace the physical scroll container')],
  ['expanded ledger rows stay mounted while idle values tick', renderer.includes('_patchExpandedLedgerRows') && renderer.includes('cell.innerHTML = freshCell.innerHTML') && !renderer.includes("copyInner('.oc-ledger-table tbody')")],
  ['expanded ledger uses normal page scrolling', styles.includes('.oc-native-scroll{') && styles.includes('overflow-y:visible') && styles.includes('max-height:none')],
  ['modal ledger keeps focused metrics views compact and scrolls All', styles.includes('table-layout:fixed') && styles.includes('.option-chain-modal-body .oc-native-scroll{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;}') && styles.includes('.oc-ledger-table[data-view="greeks"] .oc-metric:not(.greeks){display:none;}') && styles.includes('.oc-native-scroll:has(.oc-ledger-table[data-view="all"]){overflow-x:auto;}') && styles.includes('.oc-ledger-table[data-view="all"]{min-width:1380px;}')],
  ['option chain uses the shared full-screen modal', modalManager.includes('openOptionChainModal()') && modalManager.includes("this._openModal(modal, () => this.closeOptionChainModal())")],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} option-chain scroll checks passed.`);
