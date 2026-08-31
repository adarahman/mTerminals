import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '../..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const exporter = read('src/application/dashboard/serializer.py');
const capital = read('src/oi/capital_metrics.py');
const decision = read('src/core/domain.py');
const transport = read('src/application/market_cycle.py');
const docs = fs.readdirSync(path.join(root, 'docs/04_Data_Model'))
  .filter((name) => name.endsWith('.md'))
  .map((name) => read(`docs/04_Data_Model/${name}`));

const checks = [
  ['all seven data-model documents are enforced', docs.length === 7 && docs.every((doc) => doc.includes('Implemented and CI-enforced'))],
  ['snapshot publishes a versioned machine-readable contract', exporter.includes('"dataContract"') && exporter.includes('"schemaVersion": "1.0.0"')],
  ['stable row identity spans symbol expiry and strike', exporter.includes('["symbol", "expiry", "strike"]')],
  ['OI and volume units remain distinct', exporter.includes('"oi": "lot_scaled_underlying_quantity"') && exporter.includes('"volume": "contracts"')],
  ['missing and real zero remain distinct', exporter.includes('"unverifiedGreekExposure": None') && exporter.includes('"zeroMeaning": "observed_or_computed_zero"')],
  ['only contract volume is lot converted', capital.includes('out["ce_volume"] * lot_size * out["ce_ltp"]') && !capital.includes('out["ce_oi"] * lot_size')],
  ['Greek exposure fails closed when incomplete', capital.includes('if series.empty or series.isna().any():') && capital.includes('return None')],
  ['decision carries state provenance and degradation', ['decisionTimestamp','stateVersion','evidenceCoverage','missingInputs','contributors'].every((field) => decision.includes(`"${field}"`))],
  ['transport emits full and delta message types', transport.includes('"type": "full"') && transport.includes('"type": "delta"')],
  ['institutional model prohibits identity claims', docs.some((doc) => doc.includes('specific institution placed a specific option trade'))],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} data-model contract checks passed.`);
