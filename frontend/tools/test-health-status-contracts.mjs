import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const dataService = read('shared/services/data-service.js');
const template = read('Dashboard/chain/chain-template.js');
const styles = read('styles/navigation.css');
const httpAppPy = read('../src/server/http_app.py');
const healthPy = read('../src/server/health.py');
const cliArgs = read('../src/server/cli_args.py');

const checks = [
  ['backend exposes health route', httpAppPy.includes('add_get("/health", routes.health)')],
  ['health covers transport and market freshness', healthPy.includes('"websocket"') && healthPy.includes('"marketFeed"') && healthPy.includes('ageSeconds')],
  ['health reports delayed analytics separately', healthPy.includes('"analyticsPipeline"') && cliArgs.includes('pipeline-timeout-seconds')],
  ['dashboard keeps analytics delay separate from market status', dataService.includes("messageType === 'pipelineStatus'") && dataService.includes('Pipeline health is supplementary')],
  ['analytics delay does not flash in the visible Feed reason', dataService.includes("reason: prev.reason === prev.pipelineReason ? '' : prev.reason") && dataService.includes('pipelineDetail')],
  ['dashboard renders a visible feed status', template.includes('id="feed-status-pill"') && template.includes('class="feed-row"')],
  ['data-source dropdown sits below the feed status', template.includes('feed-status-pill" id="feed-status-pill"') && template.includes('id="dataSourceSelect"')],
  ['provider status is a tiny borderless readout', styles.includes('.data-source-status-pill') && styles.includes('border:0;background:transparent;padding:0')],
  ['feed capsule has stable two-column rows', styles.includes('.feed-row{display:grid;grid-template-columns:1fr auto') && styles.includes('.feed-health-pill{min-width:150px;}')],
  ['feed capsule keeps its reserved height', styles.includes('min-height:58px')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} health/feed-status checks passed.`);
