import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const helpers = read('Dashboard/chain/chain-helpers.js');
const renderer = read('Dashboard/chain/chain-renderer.js');

const checks = [
  ['ticker rows have stable keys', helpers.includes('data-index-symbol="VIX"') && helpers.includes('data-index-symbol="${backendSymbol}"')],
  ['routine ticker updates patch fields', helpers.includes('function patchIndexTicker(d)') && helpers.includes("value.textContent = fmtI(idx['Last Price'])")],
  ['ticker rebuild is structural only', helpers.includes("expected.join('|') !== existing.join('|')")],
  ['live renderer avoids ticker outerHTML replacement', renderer.includes('patchIndexTicker(d)') && !renderer.includes('tickerEl.outerHTML = html')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} system architecture checks passed.`);
