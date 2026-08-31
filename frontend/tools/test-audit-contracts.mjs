import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '../..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const historical = read('docs/07_Audits/DASHBOARD_PDS01_IMPLEMENTATION_AUDIT_CORRECTED_v1.1.md');
const current = read('docs/07_Audits/DASHBOARD_PDS01_IMPLEMENTATION_AUDIT_v1.2.md');
const pds = read('docs/01_Product_Architecture/PDS-01_Dashboard.md');
const template = read('frontend/Dashboard/chain/chain-template.js');
const sync = read('frontend/Dashboard/chain/chain-controls.js');
const modal = read('frontend/Dashboard/modal-manager.js');
const execView = read('frontend/Dashboard/exec-view.js');
const chart = read('frontend/Dashboard/chart-legend.js');
const responsive = read('frontend/styles/responsive.css');
const renderer = [
  read('frontend/Dashboard/chain/chain-dense-renderer.js'),
  read('frontend/Dashboard/chain/chain-dashboard-renderer.js'),
  read('frontend/Dashboard/chain/chain-analytics-renderer.js'),
].join('\n');
const domUtils = read('frontend/shared/utils/dom-utils.js');

const checks = [
  ['historical audit is explicitly superseded', historical.includes('Historical record — superseded') && historical.includes('SHALL NOT be read as the current')],
  ['current audit targets implementation-aligned PDS v1.3', current.includes('PDS-01_Dashboard.md` v1.3') && pds.includes('| **Version** | 1.3 |')],
  ['current audit has no open P0 or P1 violation', current.toLowerCase().includes('no open p0 or p1') && current.includes('closed compliant')],
  ['all 22 historical findings have a disposition', (current.match(/\| P[012]-\d\d /g) || []).length === 22],
  ['persistent feed status evidence exists', template.includes('id="feed-status-pill"') && template.includes('data-source-status-pill') && template.includes('id="dataSourceSelect"')],
  ['strike handoff evidence exists', sync.includes('openOptionChainAtStrike') && sync.includes('openStrikeDetailReportModal(n)')],
  ['compact breakpoint evidence exists', responsive.includes('@media (max-width:1279px)') && responsive.includes('.exec-grid{grid-template-columns:1fr;}')],
  ['modal accessibility evidence exists', modal.includes('aria-modal') && modal.includes('invoker.focus()') && modal.includes("e.key !== 'Tab'")],
  ['metric ownership evidence exists', template.includes('<span>Max Pain</span>') && execView.includes('capital-wall-owner') && execView.includes('FII / DII Cash Flow')],
  ['D-12 ledger evidence exists', execView.includes('oic-ledger-row') && execView.includes('footprintRanked')],
  ['unchanged Greeks skip redraw', chart.includes('lastGreeksSignature') && chart.includes('if(!force && signature === lastGreeksSignature && !canvasChanged) return;')],
  ['residual performance risks have triggers', current.includes('R-01 — Hot-card patch granularity') && current.includes('R-02 — Deferred heavy rendering') && current.includes('Trigger:')],
  ['closed heavy modals skip live rendering', domUtils.includes('function isModalOpen') && renderer.includes("if(isModalOpen('greeks-dashboard-modal')) renderGreeksGex") && renderer.includes("if(isModalOpen('iv-surface-modal')) this.renderIvSurfaceModal") && modal.includes('if(window.renderGreeksGex) renderGreeksGex') && modal.includes('app.chain.renderIvSurfaceModal()')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} audit contract checks passed.`);
