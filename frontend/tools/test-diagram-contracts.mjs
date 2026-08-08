import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '../..');
const dir = path.join(root, 'docs/06_Diagrams');
const names = fs.readdirSync(dir).filter((name) => name.endsWith('.md')).sort();
const files = Object.fromEntries(names.map((name) => [name, fs.readFileSync(path.join(dir, name), 'utf8')]));
const all = Object.values(files).join('\n');

const balancedMermaid = (doc) => {
  const fences = doc.match(/```mermaid|```/g) || [];
  return fences.length === 2 && fences[0] === '```mermaid' && fences[1] === '```';
};
const checks = [
  ['all nine diagrams are present', names.length === 9],
  ['every diagram records implementation status', Object.values(files).every((doc) => doc.includes('Status:** Implemented and CI-enforced'))],
  ['every document has one balanced Mermaid block', Object.values(files).every(balancedMermaid)],
  ['system flow includes acquisition through canonical store', ['Market acquisition','Decision engine','Full / delta WebSocket transport','MarketStore + AppState'].every((x) => files['System_Overview.md'].includes(x))],
  ['rendering rejects incompatible baselines', files['Rendering_Pipeline.md'].includes('Compatible baseline?') && files['Rendering_Pipeline.md'].includes('request full snapshot')],
  ['feed diagram distinguishes session overlays', files['State_Diagram.md'].includes('MARKET_CLOSED') && files['State_Diagram.md'].includes('session overlays')],
  ['event flow keeps snapshots out of EventBus', files['Event_Flow.md'].includes('Market snapshots stay in') && files['Event_Flow.md'].includes('market:update metadata')],
  ['dashboard layout covers D-00 through D-19', Array.from({length: 20}, (_, i) => `D-${String(i).padStart(2, '0')}`).every((id) => files['Dashboard_Layout.md'].includes(id))],
  ['navigation restores drill-down context', files['Navigation.md'].includes('restore context') && files['Navigation.md'].includes('selected strike')],
  ['user flow keeps live execution explicitly gated', files['User_Flow.md'].includes('Explicit live') && files['User_Flow.md'].includes('risk gates')],
  ['metric dependencies isolate scenario state', files['Metric_Dependency.md'].includes('never mutates live state') && files['Metric_Dependency.md'].includes('Nullable delta / gamma exposure')],
  ['no diagram remains a design target', !all.includes('Authoritative design target unless marked otherwise')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} diagram contract checks passed.`);
