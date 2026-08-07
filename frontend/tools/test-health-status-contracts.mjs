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
  ['dashboard renders a visible feed reason', template.includes('id="feed-status-reason"')],
  ['feed reason updates without a full render', dataService.includes("$i('feed-status-reason')") && dataService.includes('reasonEl.textContent = reasonText')],
  ['feed reason uses truncation-safe styling', styles.includes('.feed-status-reason') && styles.includes('text-overflow:ellipsis')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} health/feed-status checks passed.`);
