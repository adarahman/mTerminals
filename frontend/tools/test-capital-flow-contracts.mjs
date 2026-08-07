import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const flow = read('Dashboard/oi-flow-view.js');
const strike = read('Dashboard/strike-detail-report-view.js');
const exec = read('Dashboard/exec-view.js');
const backend = read('../backend/mTerminals_json.py');
const capital = read('../backend/oi/capital_metrics.py');
const panelsCss = read('styles/panels.css');

const checks = [
  ['flow is explicitly day-session scope', flow.includes('Day-session ΔOI × LTP · visible range')],
  ['flow and concentration remain separate', flow.includes('capital-flow-section') && exec.includes('Capital Concentration')],
  ['Stage-1 foundation is visible', ['Premium locked','Premium turnover','Gross strike notional'].every((x) => flow.includes(x))],
  ['large values use lakh-crore scaling', flow.includes("a>=1e12") && flow.includes("lakh Cr")],
  ['gross notional is distinguished from deployed premium', flow.includes('Gross strike notional') && flow.includes('not cash deployed')],
  ['flow interpretation avoids transaction claims', flow.includes('not proof of fresh buying') && flow.includes('not proof of fresh writing')],
  ['day-session flow is separated from intraday velocity', flow.includes('Capital Flow is day-session') && flow.includes('separate intraday velocity')],
  ['visible concentration context is provided', flow.includes('holds ${fmtN(topCapitalPct,1)}% of visible premium locked')],
  ['print layout keeps Capital Flow together', panelsCss.includes('@media print') && panelsCss.includes('break-inside:avoid-page')],
  ['monetary units and lot contract are visible', flow.includes('OI is already lot-scaled') && flow.includes('turnover alone converts raw volume contracts')],
  ['Stage-2 units and availability are qualified', strike.includes('Stage-2 exposures require verified live Greeks') && strike.includes('Delta spot-notional')],
  ['nullable Stage-2 values cross JSON boundary', backend.includes('"netGammaExposureCapital": _nullable_r') && backend.includes('"netDeltaExposureCapital": _nullable_r')],
  ['missing Greeks are masked canonically', capital.includes('ce_greeks_valid') && capital.includes('.where(ce_greeks_valid)')],
  ['only raw volume receives lot-size conversion', capital.includes('out["ce_volume"] * lot_size * out["ce_ltp"]') && !capital.includes('out["ce_oi"] * lot_size')],
  ['FII/DII is labeled cash market', exec.includes('FII / DII Cash Flow') && exec.includes('Cash market')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} Capital Flow contracts passed.`);
