import assert from 'node:assert/strict';
import fs from 'node:fs';

const simulator = fs.readFileSync(new URL('../Dashboard/simulator-view.js', import.meta.url), 'utf8');
const renderer = fs.readFileSync(new URL('../Dashboard/chain/chain-dashboard-renderer.js', import.meta.url), 'utf8');
const dashboard = fs.readFileSync(new URL('../Dashboard/dashboard.js', import.meta.url), 'utf8');
const scenario = fs.readFileSync(new URL('../Dashboard/scenario-analysis-view.js', import.meta.url), 'utf8');
const backtest = fs.readFileSync(new URL('../Dashboard/backtest-view.js', import.meta.url), 'utf8');

assert.match(renderer, /Live Net GEX/, 'neutral simulator must identify the live GEX baseline');
assert.match(simulator, /Scenario-Adjusted Gamma Flip/, 'derived gamma flip must be scenario-qualified');
assert.match(simulator, /Scenario Dealer Regime/, 'derived dealer regime must be scenario-qualified');
assert.match(renderer, /Reset to Live/, 'scenario must have an explicit reset action');
assert.match(simulator, /isLiveBaseline[\s\S]*Live Baseline[\s\S]*Scenario-Adjusted/, 'simulator must label live and adjusted GEX modes dynamically');
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
assert.match(scenario, /P&amp;L \/ unit \(gross\)/,
  'scenario payoff must identify its unit and gross basis');
assert.match(scenario, /excludes lot multiplier, brokerage, taxes, fees and slippage/,
  'scenario payoff assumptions must disclose excluded costs');
assert.match(backtest, /snapshotCount/,
  'backtest must show the historical sample size');
assert.match(backtest, /transaction costs.*excluded/s,
  'backtest must disclose whether transaction costs are included');
assert.match(backtest, /does not recompute historical Decision Engine scoring/,
  'backtest must explain that captured decisions are replayed rather than rescored');

console.log('Scenario contracts: 20/20 passed');
