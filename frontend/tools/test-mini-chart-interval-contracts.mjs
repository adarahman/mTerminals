import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const template = fs.readFileSync(
  path.join(root, 'Dashboard/chain/chain-template.js'), 'utf8');

const checks = [
  ['mini chart maps the selected 5m interval to five minutes', template.includes("'5m': 5 * 60 * 1000")],
  ['live ticks use a fixed interval bucket timestamp', template.includes('bucketStart = Math.floor(Date.now() / intervalMs) * intervalMs')],
  ['ticks inside the active bucket update the candle in place', template.includes('last.t === bucketStart') && template.includes('last.c = spotNum')],
  ['a new candle is appended only after the bucket advances', template.includes('last.t < bucketStart')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} mini-chart interval checks passed.`);
