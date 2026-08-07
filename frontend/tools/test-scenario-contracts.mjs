import assert from 'node:assert/strict';
import fs from 'node:fs';

const simulator = fs.readFileSync(new URL('../Dashboard/simulator-view.js', import.meta.url), 'utf8');
const renderer = fs.readFileSync(new URL('../Dashboard/chain/chain-renderer.js', import.meta.url), 'utf8');
const dashboard = fs.readFileSync(new URL('../Dashboard/dashboard.js', import.meta.url), 'utf8');

assert.match(renderer, /Scenario Net GEX/, 'derived GEX must be scenario-qualified');
assert.match(renderer, /Scenario-Adjusted Gamma Flip/, 'derived gamma flip must be scenario-qualified');
assert.match(renderer, /Scenario Dealer Regime/, 'derived dealer regime must be scenario-qualified');
assert.match(renderer, /Reset Scenario/, 'scenario must have an explicit reset action');
assert.match(dashboard, /window\.resetScenario/, 'reset action must be callable from rendered controls');
assert.match(simulator, /resetScenario\(\)[\s\S]*simSpotOverride = null[\s\S]*simIvOverride = null[\s\S]*simUpdate\(\)/,
  'reset must clear local overrides and recompute without reloading market data');
assert.match(simulator, /_syncPristineControlsToLive[\s\S]*simSpotOverride == null/,
  'untouched controls must track live reference values');
assert.match(simulator, /velEl[\s\S]*simVelOverride == null[\s\S]*simState\.vel/,
  'reset must restore the separate velocity scenario control');
assert.doesNotMatch(simulator, /resetScenario\(\)[\s\S]{0,500}(?:reload|fetch|connect)\s*\(/,
  'reset must not reload or reconnect market data');
assert.doesNotMatch(renderer, /if\s*\(strats\.length\)\s*\{[\s\S]{0,1800}const simCtx/,
  'Institutional Simulator must not be gated by strategy availability');
assert.match(renderer, /grid-template-columns:\$\{strats\.length\?'1fr 1fr':'1fr'\}/,
  'simulator must expand to full width when Strategy Payoff is unavailable');

console.log('Scenario contracts: 11/11 passed');
