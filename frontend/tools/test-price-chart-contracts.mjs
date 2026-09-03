import assert from 'node:assert/strict';
import fs from 'node:fs';

const chart = fs.readFileSync(new URL('../PriceChart/price-chart-engine.js', import.meta.url), 'utf8');
const history = fs.readFileSync(new URL('../PriceChart/history-loader.js', import.meta.url), 'utf8');
const dashboard = fs.readFileSync(new URL('../Dashboard/chain/chain-template.js', import.meta.url), 'utf8');
const dashboardHtml = fs.readFileSync(new URL('../Dashboard/DashboardPro.html', import.meta.url), 'utf8');
const modal = fs.readFileSync(new URL('../Dashboard/modal-manager.js', import.meta.url), 'utf8');
const dataService = fs.readFileSync(new URL('../shared/services/data-service.js', import.meta.url), 'utf8');
const build = fs.readFileSync(new URL('../build.mjs', import.meta.url), 'utf8');
const generator = fs.readFileSync(new URL('../gen_html.mjs', import.meta.url), 'utf8');

assert.match(dashboard, /onclick="openPriceChartModal\(\)"/,
  'dashboard mini chart must open the native Price Chart modal');
assert.match(dashboardHtml, /id="price-chart-modal"[\s\S]*id="price-chart-modal-host"/,
  'DashboardPro must own the Price Chart modal and mount host');
assert.match(modal, /openPriceChartModal\(\)[\s\S]*priceChart\.ensureMounted\(\)[\s\S]*hydrateRange/,
  'opening the modal must mount and hydrate the native chart');
assert.match(dataService, /window\.priceChart\.addTick/,
  'the dashboard feed must keep the native chart buffer current');
assert.match(chart, /this\._zoomStart[\s\S]*this\._zoomEnd/,
  'zoom/pan state must be stored independently of live ticks');
assert.match(chart, /addTick\(price, t, vwap\)\{[\s\S]*?this\.chartData\.addTick\(price, t, vwap\);[\s\S]*?this\._scheduleRender\(\);/,
  'a live tick must schedule a repaint on its own — the canvas must not depend on cursor movement to show new candles');
assert.match(history, /catch \(e\)[\s\S]*Logger\.warn\('historyLoader'/,
  'history failures must be contained without breaking live quote handling');
assert.match(build, /Dashboard\/DashboardPro\.html[\s\S]*PriceChart\/price-chart-engine\.js/,
  'Dashboard production bundle must include the native chart engine');
assert.doesNotMatch(build, /html: "PriceChart\/price-chart\.html"/,
  'build must not emit a second PriceChart HTML page');
assert.doesNotMatch(generator, /src: "PriceChart\/price-chart\.html"/,
  'HTML generation must keep DashboardPro as the only page');

console.log('Price chart contracts: 10/10 passed');
