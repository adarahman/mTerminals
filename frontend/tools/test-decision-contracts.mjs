import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../Dashboard/chain/chain-template.js', import.meta.url), 'utf8');

assert.match(source, /Evidence Confidence/, 'confidence must be labelled as evidence confidence');
assert.match(source, /dec\.evidenceCoverage/, 'decision view must consume backend evidence coverage');
assert.match(source, /dec\.contributors/, 'decision view must expose backend contributors');
assert.match(source, /dec\.degraded/, 'decision view must expose degraded state');
assert.match(source, /dec\.missingInputs/, 'decision view must expose missing inputs');
assert.doesNotMatch(source, /confidence\s*=\s*.*(?:pcr|oi_score|composite)/i,
  'frontend must not recompute decision confidence');

console.log('Decision contracts: 6/6 passed');
