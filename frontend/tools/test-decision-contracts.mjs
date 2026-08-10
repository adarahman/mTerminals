import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../Dashboard/chain/chain-template.js', import.meta.url), 'utf8');
const dashboardRenderer = fs.readFileSync(new URL('../Dashboard/chain/chain-dashboard-renderer.js', import.meta.url), 'utf8');
const strategyView = fs.readFileSync(new URL('../Dashboard/strategy-view.js', import.meta.url), 'utf8');

assert.match(source, /Evidence Confidence/, 'confidence must be labelled as evidence confidence');
assert.match(source, /dec\.evidenceCoverage/, 'decision view must consume backend evidence coverage');
assert.match(source, /dec\.contributors/, 'decision view must expose backend contributors');
assert.match(source, /dec\.degraded/, 'decision view must expose degraded state');
assert.match(source, /dec\.missingInputs/, 'decision view must expose missing inputs');
assert.doesNotMatch(source, /confidence\s*=\s*.*(?:pcr|oi_score|composite)/i,
  'frontend must not recompute decision confidence');
assert.match(source, /escapeHtml\(s\.text\)/,
  'active signal text must be escaped before insertion into the dashboard');
assert.match(source, /escapeHtml\(c\.label \|\| c\.key \|\| 'Signal'\)/,
  'expanded contributor labels must be escaped before insertion');
assert.match(source, /data-signal-id=/,
  'active signals must expose their stable backend identity');
assert.match(source, /signalObservedAt/,
  'active signals must expose their observation time');
assert.match(source, /signalFreshness/,
  'active signals must show current feed freshness');
assert.match(dashboardRenderer, /d\.decision\.suggestedStrategy/,
  'strategy section must default to the Decision Engine recommendation');
assert.match(strategyView, /this\.selectionTouched = true/,
  'manual strategy selection must remain under user control');

console.log('Decision contracts: 13/13 passed');
