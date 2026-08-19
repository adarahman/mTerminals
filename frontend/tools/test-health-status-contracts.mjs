import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const dataService = read('shared/services/data-service.js');
const template = read('Dashboard/chain/chain-template.js');
const styles = read('styles/navigation.css');
const server = read('../ws_server_live.py');

const checks = [
  ['backend exposes health route', server.includes("app.router.add_get('/health', health_handler)")],
  ['health covers transport and market freshness', server.includes('"websocket"') && server.includes('"marketFeed"') && server.includes('ageSeconds')],
  ['health reports delayed analytics separately', server.includes('"analyticsPipeline"') && server.includes('PIPELINE_TIMEOUT_SECONDS')],
  ['dashboard keeps analytics delay separate from market status', dataService.includes("messageType === 'pipelineStatus'") && dataService.includes('Pipeline health is supplementary')],
  ['analytics delay does not flash in the visible Feed reason', dataService.includes("reason: prev.reason === prev.pipelineReason ? '' : prev.reason") && dataService.includes('pipelineDetail')],
  ['dashboard renders a visible feed status', template.includes('id="feed-status-pill"') && template.includes('feed-source-row')],
  ['data-source dropdown sits below the feed status', template.includes('feed-status-pill" id="feed-status-pill"') && template.includes('id="dataSourceSelect"')],
  ['provider status is a tiny borderless readout', styles.includes('.data-source-status-pill') && styles.includes('border:0;background:transparent;padding:0')],
  ['feed capsule has stable fixed tracks', styles.includes('grid-template-columns:122px 1px 150px 1px minmax(150px,1fr) 1px 62px') && styles.includes('flex:0 0 545px')],
  ['feed capsule keeps its reserved height', styles.includes('min-height:58px')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} health/feed-status checks passed.`);
