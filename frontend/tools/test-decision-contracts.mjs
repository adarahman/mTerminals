import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../Dashboard/chain/chain-template.js', import.meta.url), 'utf8');
const dashboardRenderer = fs.readFileSync(new URL('../Dashboard/chain/chain-dashboard-renderer.js', import.meta.url), 'utf8');
const strategyView = fs.readFileSync(new URL('../Dashboard/strategy-view.js', import.meta.url), 'utf8');

assert.match(source, /Evidence Confidence/, 'confidence must be labelled as evidence confidence');
assert.match(source, /dec\.evidenceCoverage/, 'decision view must consume backend evidence coverage');
assert.match(source, /dec\.contributors/, 'decision view must expose backend contributors');
assert.match(source, /\['futures', 'Futures positioning', 20\]/,
  'decision detail must retain a visible futures-positioning row when evidence is missing');
assert.match(source, /dec\.degraded/, 'decision view must expose degraded state');
assert.match(source, /dec\.missingInputs/, 'decision view must expose missing inputs');
assert.match(source, /dec\.tradeability/, 'decision view must consume backend tradeability');
assert.match(source, /dec\.tradeabilityReason/, 'decision view must consume backend tradeability reason');
assert.match(source, /dec\.confirmationLevels/, 'decision view must consume backend confirmation levels');
assert.match(source, /renderDecisionActionBarHtml/, 'dashboard must expose the sticky trade decision summary');
assert.match(source, /confirmation\.aboveResistance/, 'sticky summary must use backend long confirmation level');
assert.match(source, /confirmation\.belowSupport/, 'sticky summary must use backend short confirmation level');
assert.match(source, /Stop \/ target/, 'sticky summary must make unavailable risk levels explicit');
assert.match(dashboardRenderer, /renderDecisionActionBarHtml\(d\)/, 'sticky summary must be rendered outside the detail panel');
assert.match(dashboardRenderer, /planEl\.outerHTML = this\.renderDecisionActionBarHtml\(d\)/, 'sticky summary must refresh on live ticks');
assert.match(source, /window\.fetchMarketHistory\(symbol, '1m'/, 'session context must reuse the shared one-minute market-history cache');
assert.match(source, /openingHigh:openingBars/, 'session context must compute the opening range from session candles');
assert.match(source, /weightedPrice \/ tradedVolume/, 'VWAP must use candle volume and become unavailable when volume is missing');
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

console.log('Decision contracts passed');
