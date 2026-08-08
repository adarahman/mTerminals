import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const view = read('Dashboard/strike-detail-report-view.js');
const modal = read('Dashboard/modal-manager.js');
const simulator = read('Dashboard/simulator-view.js');
const sync = read('Dashboard/chain/chain-sync.js');
const oc = read('OptionChain/option-chain.js');
const backend = read('../backend/mTerminals_json.py');
const build = read('build.mjs');

const checks = [
  ['selected strike is mandatory', modal.includes('!Number.isFinite(n)') && modal.includes('app.strikeDetail.render(n)')],
  ['canonical chain row is the base', view.includes('(d.chain || []).find') && !view.includes('simState')],
  ['Greeks are optional enrichment', view.includes('(d.greeks || []).find') && view.includes('Greeks unavailable; core chain, capital and flow remain live')],
  ['canonical footprint factors are exported', backend.includes('"footprintFactors"') && backend.includes('footprint_pct_capital_activity')],
  ['full footprint contributor breakdown is explained', view.includes('Footprint score contributors') && !view.includes('.slice(0,3)') && view.includes('relative to the currently visible chain')],
  ['feed state and timestamp are explicit', view.includes('Timestamp ${this._escape(asOf)}') && view.includes("replaceAll('_', ' ')")],
  ['5/15/30 velocity context is rendered', ['5m OI velocity','15m OI velocity','30m OI velocity'].every((label) => view.includes(label))],
  ['partial feed is qualified', view.includes("fs.quality === 'PARTIAL'") && view.includes('sdr-feed-note')],
  ['simulator does not render Strike Detail', !simulator.includes('this.simRenderTable(simGEX')],
  ['D-05 explicit action remains', sync.includes('openStrikeDetailReportModal(msg.strike)') && (oc.match(/Open Strike Detail/g) || []).length >= 2],
  ['production bundle includes report view', build.includes('Dashboard/strike-detail-report-view.js')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} Strike Detail contracts passed.`);
