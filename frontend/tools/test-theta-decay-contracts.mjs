import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..');
const source = fs.readFileSync(
  path.join(root, 'Dashboard/chain/chain-greeks.js'),
  'utf8',
);

function createHarness() {
  let now = 100000;
  const feedState = {
    status: 'LIVE',
    quality: 'FULL',
    pipelineDelayed: false,
    lastMessageAt: now,
  };
  const context = {
    window: {
      AppState: { feedState },
    },
    Config: { ws: { staleAfterMs: 30000 } },
    Date: { now: () => now },
    ChainView: function ChainView() {},
    computeNetGEX: () => 0,
    computeGammaFlip: () => null,
    fmtI: (value) => String(value),
    fmtN: (value, digits) => Number(value).toFixed(digits),
  };
  vm.runInNewContext(source, context, { filename: 'chain-greeks.js' });

  const greek = { strike: 100, cTheta: -1, pTheta: -1 };
  const row = {
    strike: 100,
    ceBid: 9,
    ceAsk: 11,
    peBid: 19,
    peAsk: 21,
    ceLTP: 10,
    peLTP: 20,
  };
  const data = {
    symbol: 'TEST',
    expiry: '20-Nov-2026',
    chain: [row],
  };
  const render = (greeks = [greek]) =>
    new context.ChainView().buildGreeksAlertsHtml(greeks, 100, data);
  const setSnapshot = (timestamp) => {
    now = timestamp;
    feedState.lastMessageAt = timestamp;
  };
  return { data, feedState, greek, render, row, setSnapshot };
}

const baseline = createHarness();
const baselineHtml = baseline.render();
assert.match(baselineHtml, /THETA WATCH · ATM STRADDLE/);
assert.match(baselineHtml, /6\.7%\/day/);
assert.match(baselineHtml, /mid-price/);
assert.match(baselineHtml, /High modeled decay/);
assert.match(baselineHtml, /Not a trade signal/);
assert.match(baselineHtml, /data-alert-kind="theta-watch" role="status" aria-live="polite"/);
assert.ok(
  baselineHtml.indexOf('THETA WATCH · ATM STRADDLE')
    < baselineHtml.indexOf('VANNA'),
  'theta watch should appear before secondary Greek context',
);

const fallback = createHarness();
fallback.row.ceBid = 0;
fallback.row.ceAsk = 0;
fallback.row.peBid = 0;
fallback.row.peAsk = 0;
assert.match(fallback.render(), /LTP proxy \(mid-price unavailable\)/);

const trend = createHarness();
trend.setSnapshot(100000);
trend.render();
trend.row.ceBid = 9;
trend.row.ceAsk = 11;
trend.row.peBid = 18.5;
trend.row.peAsk = 20.5;
trend.setSnapshot(103000);
trend.render();
trend.row.peBid = 18;
trend.row.peAsk = 20;
trend.setSnapshot(106000);
assert.match(trend.render(), /Rate rising across 3 fresh snapshots/);
assert.match(trend.render(), /Rate rising across 3 fresh snapshots/);

const stale = createHarness();
stale.feedState.status = 'STALE';
assert.match(stale.render(), /Unavailable — Live market snapshot is unavailable or stale/);

const missingGreek = createHarness();
missingGreek.greek.cTheta = null;
assert.match(missingGreek.render(), /Unavailable — ATM call\/put theta is unavailable/);

console.log('PASS  theta decay metric, live-state gates, LTP fallback and trend');
