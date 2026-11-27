import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const sync = read('Dashboard/chain/chain-controls.js');
const template = read('Dashboard/chain/chain-template.js');
const commodityPrices = read('Dashboard/commodity-prices.js');
const dashboardHtml = read('Dashboard/DashboardPro.html');
const styles = read('styles/panels.css');
const commodityStyles = read('styles/commodity-prices.css');
const paperStyles = read('styles/paper-trading.css');
const modalManager = read('Dashboard/modal-manager.js');
const build = read('build.mjs');
const spec = read('../docs/01_Product_Architecture/PDS-02_Option_Chain.md');
const rangeTabs = read('Dashboard/range-tabs.js');
const chainView = read('Dashboard/chain/chain-view.js');
const analyticsRenderer = read('Dashboard/chain/chain-analytics-renderer.js');
const server = read('../src/server/cli_args.py');

const checks = [
  ['standalone Option Chain page is not built', !build.includes('OptionChain/option-chain.html')],
  ['strike navigation stays in dashboard', sync.includes('openStrikeDetailReportModal(n)') && !sync.includes('window.open(')],
  ['chain header no longer advertises a duplicate full page', !template.includes('Open full option chain')],
  ['snapshot header has left SVG-only Option Chain action and range dropdown',
    template.includes('class="oi-snap-open-link"') &&
    template.includes('onclick="openOptionChainModal(this)"') &&
    template.includes('aria-label="Open Option Chain"') &&
    template.includes('title="Open Option Chain"') &&
    template.includes('<svg viewBox="0 0 16 16" aria-hidden="true">') &&
    template.includes('data-chain-range-select') &&
    template.includes('buildRangeSelectOptionsHtml(_chainRange)') &&
    !template.includes('Option Chain <span aria-hidden="true">↗</span>') &&
    template.indexOf('class="oi-snap-open-link"') <
      template.indexOf('class="oi-snap-header-actions"') &&
    sync.includes("openOptionChainModal(button)")],
  ['rail does not duplicate OI Flow', !dashboardHtml.includes('id="oi-flow-open-btn"') && dashboardHtml.includes('data-primary-nav="market"') && template.includes('aria-label="Open OI Flow chart"')],
  ['live ticks preserve an open range dropdown', analyticsRenderer.includes("document.activeElement.matches('[data-chain-range-select]')") && analyticsRenderer.includes('return rangeSelectOpen ||')],
  ['metric views use a Price Chart-style context rail while All columns stays dense', dashboardHtml.includes('id="option-chain-focus-grid"') && dashboardHtml.includes('id="option-chain-context"') && modalManager.includes("chainLedgerView==='all'") && modalManager.includes("layout.classList.toggle('dense-all',allColumns)") && sync.includes('renderOptionChainContext(_data)') && styles.includes('.option-chain-focus-grid.dense-all .option-chain-context{display:none;}')],
  ['range selection releases the live-tick guard before recalculation', chainView.includes("el.matches('[data-chain-range-select]')") && chainView.includes('el.blur();') && chainView.indexOf('el.blur();') < chainView.indexOf('if(_data) _rerenderChainPanels();')],
  ['LTP cells restore Buy Sell quick order', template.includes("ptOpenQuickOrder(event") && template.includes('Buy or sell')],
  ['LTP Buy Sell popup appears above modal', paperStyles.includes('z-index:calc(var(--z-modal) + 2)') && modalManager.includes("quickOrder.style.display = 'none'")],
  ['chain metric tabs expose Positioning, Greeks, and All without Activity, with IV left of Greeks', dashboardHtml.includes('data-chain-view="positioning"') && dashboardHtml.includes('data-chain-view="greeks"') && dashboardHtml.includes('data-chain-view="all"') && !dashboardHtml.includes('data-chain-view="activity"') && template.includes('<th class="oc-metric activity positioning greeks">IV <small>PE / CE</small></th>') && template.indexOf('>IV <small>PE / CE</small></th>') < template.indexOf('>Delta <small>PE / CE</small></th>') && template.includes('oc-metric greeks') && template.includes('Delta <small>PE / CE</small>') && template.includes('Gamma <small>PE / CE</small>') && !styles.includes('.oc-ledger-table[data-view="greeks"] .c-iv') && !template.includes('oc-ledger-greeks') && sync.includes("['positioning', 'activity', 'greeks', 'all']")],
  ['equity positioning chain uses the same compact, balanced layout', styles.includes('.oc-ledger-table[data-view="positioning"]{min-width:0;font-size:10px;}') && styles.includes('.oc-native-scroll:has(.oc-ledger-table[data-view="positioning"]){overflow-x:hidden;}') && styles.includes('.oc-ledger-table[data-view="positioning"] .c-sig{width:10%}') && styles.includes('.oc-ledger-table[data-view="positioning"] .c-ltp{width:9%}') && styles.includes('.oc-ledger-table[data-view="positioning"] .c-strike{width:8%}') && styles.includes('.oc-ledger-table[data-view="positioning"] .c-capital{width:12%}')],
  ['commodity mode does not hide Greeks ledger columns', !commodityStyles.includes('.oc-ledger-table .oc-metric.greeks{display:none')],
  ['commodity decision card displays ATM IV and does not imply a missing trigger', template.includes("const isCommodity = d.instrument === 'MCX_FUT'") && template.includes('Informational only · no execution trigger') && commodityPrices.includes("atmIV: Number.isFinite(atmIV) ? `${atmIV.toFixed(2)}%` : undefined")],
  ['dashboard CE/PE identity matches the ledger palette', styles.includes('.strike-link.ce{color:var(--ce);}') && styles.includes('.strike-link.pe{color:var(--pe);}') && styles.includes('.oi-snap-sides .ce{color:var(--ce);background:color-mix') && styles.includes('.oi-snap-sides .pe{color:var(--pe);background:color-mix')],
  ['dense ledger separates CE/PE identity from price direction', styles.includes('--oc-ce:var(--ce);--oc-pe:var(--pe)') && styles.includes('.oc-ledger-stack .ce{color:var(--oc-ce)}') && styles.includes('.oc-ledger-stack .pe{color:var(--oc-pe)}') && styles.includes('td.ltp.ce strong{color:var(--oc-ce)}') && styles.includes('td.ltp.pe strong{color:var(--oc-pe)}')],
  ['ledger signal styles match the generated signal classes', ['sig-strongbull','sig-strongbear','sig-mixed','sig-bull','sig-bear'].every(signalClass => styles.includes(`.oc-ledger-signal.${signalClass}`))],
  ['composite signal is the leftmost ledger column', template.indexOf('<col class="c-sig">') < template.indexOf('<col class="c-ltp">') && template.indexOf('<th class="oc-metric positioning">Signal') < template.indexOf('<th class="ce">CE LTP') && template.indexOf('<td class="oc-metric positioning signal">') < template.indexOf('<td class="ltp ce">')],
  ['snapshot exposes CE and PE totals separately', ['CE OI','PE OI','CE ΔOI','PE ΔOI'].every(label => template.includes(label))],
  ['Positioning ledger includes Capital Flow PE/CE', template.includes('class="c-capital"') && template.includes('Capital Flow <small>') && template.includes("'PE / CE'") && template.includes('r.peCapitalFlow') && template.includes('r.ceCapitalFlow')],
  ['Positioning ledger includes paired Volume, IV, and Premium metrics in both modes', template.includes('<td class="oc-metric activity positioning">') && template.includes('<th class="oc-metric activity positioning">Volume <small>PE / CE</small></th>') && template.includes('<th class="oc-metric activity positioning greeks">IV <small>PE / CE</small></th>') && template.includes('<th class="oc-metric activity positioning">Premium ₹ <small>PE / CE</small></th>') && template.includes('formatLtpAmount(r.ceChg)') && template.includes('formatLtpAmount(r.peChg)') && styles.includes('body.commodity-mode .oc-ledger-table[data-view="positioning"]{min-width:0;font-size:10px;}')],
  ['commodity positioning columns fit compactly around Strike and center the LTP heading', template.indexOf('<th class="oc-metric activity positioning greeks">IV <small>PE / CE</small></th><th class="oc-metric activity positioning">Premium ₹') < template.indexOf('<th class="ltp">LTP <small>PE / CE</small></th>') && template.indexOf('<th class="ltp">LTP <small>PE / CE</small></th>') < template.indexOf('<th class="strike">Strike') && template.indexOf('<th class="strike">Strike') < template.indexOf('<th class="oc-metric positioning">Open Int') && template.includes('<col class="c-sig"><col class="c-vol"><col class="c-iv"><col class="c-prem"><col class="c-ltp">') && styles.includes('body.commodity-mode .oc-ledger-table[data-view="positioning"] .c-sig{width:8%}') && styles.includes('body.commodity-mode .oc-ledger-table[data-view="positioning"] .c-ltp{width:14%}') && styles.includes('body.commodity-mode .oc-ledger-table[data-view="positioning"] .c-strike{width:8%}') && styles.includes('body.commodity-mode .oc-ledger-table[data-view="positioning"] .c-oi{width:13%}') && styles.includes('body.commodity-mode .oc-ledger-table[data-view="positioning"] .c-chg{width:13%}') && styles.includes('body.commodity-mode .oc-ledger-table[data-view="positioning"] .c-capital{width:17%}') && styles.includes('body.commodity-mode .oc-ledger-table[data-view="positioning"] th.ltp{text-align:center;') && styles.includes('body.commodity-mode .oc-native-scroll:has(.oc-ledger-table[data-view="positioning"]){overflow-x:hidden;}')],
  ['PE and CE LTP changes share one commodity column', template.includes('LTP <small>PE / CE</small>') && template.indexOf('side-value pe ltp-change-row') < template.indexOf('side-value ce ltp-change-row')],
  ['commodity LTP and change share one line with smaller change text and percent on hover', template.includes('side-value pe ltp-change-row') && template.includes('side-value ce ltp-change-row') && template.includes('title="${formatLtpPercent(r.peChgPct)}"') && template.includes('title="${formatLtpPercent(r.ceChgPct)}"') && styles.includes('.oc-ledger-stack .side-value.ltp-change-row{display:flex;flex-direction:row;') && styles.includes('.oc-ledger-table td.ltp .oc-ledger-stack .ltp-change-row small{display:inline-block;') && styles.includes('font-size:9px;font-weight:500;')],
  ['net OI is the sole positioning headline', template.includes('class="oi-snap-overview"') && template.includes('class="oi-snap-head-net"><small>Net OI · PE−CE') && template.includes('class="oi-snap-head-net"><small>Net ΔOI') && !template.includes('<small>Total OI</small>') && !template.includes('Net positioning · PE−CE')],
  ['PCR change uses ratio precision instead of OI units', template.includes('const signedPcrDelta') && template.includes('signedPcrDelta(pcrShift)')],
  ['structure column uses shared per-strike classifier', template.includes('marketStructureLabels(chain') && template.includes('structureByStrike[Number(r.strike)]')],
  ['ATM ±5 is the fallback and the selected frontend range persists independently of backend analytics width', rangeTabs.includes('CHAIN_RANGE_FALLBACK = 5') && rangeTabs.includes('CHAIN_RANGE_STORAGE_KEY') && chainView.includes('this.chainRange = RANGE_TAB_DEFAULT') && chainView.includes('localStorage.setItem(CHAIN_RANGE_STORAGE_KEY') && server.includes('default=15')],
  ['PDS permits paired bilateral stacks', spec.includes('paired bilateral PE/CE stacks')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} Option Chain contract checks passed.`);
