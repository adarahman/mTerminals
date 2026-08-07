import assert from 'node:assert/strict';
import fs from 'node:fs';

const standalone = fs.readFileSync(new URL('../PriceChart/price-chart-standalone.js', import.meta.url), 'utf8');
const chart = fs.readFileSync(new URL('../PriceChart/price-chart.js', import.meta.url), 'utf8');
const history = fs.readFileSync(new URL('../PriceChart/history-loader.js', import.meta.url), 'utf8');
const dashboard = fs.readFileSync(new URL('../Dashboard/chain/chain-template.js', import.meta.url), 'utf8');
const dashboardHtml = fs.readFileSync(new URL('../Dashboard/DashboardPro.html', import.meta.url), 'utf8');
const build = fs.readFileSync(new URL('../build.mjs', import.meta.url), 'utf8');

assert.match(dashboard, /price-chart\.html\?symbol=\$\{encodeURIComponent\(d\.symbol\|\|'NIFTY'\)\}/,
  'dashboard chart action must preserve the active symbol');
assert.match(standalone, /URLSearchParams\(location\.search\)\.get\('symbol'\)/,
  'standalone chart must consume the synchronized symbol');
assert.match(standalone, /state\.decision/, 'chart header must consume live Decision Engine context');
assert.match(standalone, /requestAnimationFrame[\s\S]*priceChart\.render\(\)/,
  'live chart and indicator rendering must be scheduled outside the socket callback');
assert.match(chart, /this\._zoomStart[\s\S]*this\._zoomEnd/,
  'zoom/pan state must be stored independently of live ticks');
assert.match(history, /catch \(e\)[\s\S]*Logger\.warn\('historyLoader'/,
  'history failures must be contained without breaking live quote handling');
assert.doesNotMatch(standalone, /function onStateChange[\s\S]{0,700}priceChart\.render\(\)/,
  'WebSocket state callback must not render synchronously');
assert.doesNotMatch(dashboardHtml, /PriceChart\/(?:chart-data|chart-renderer|indicator-engine|history-loader|price-chart)\.js/,
  'Dashboard must not load the standalone chart analytics engine');
const dashboardPageBlock = build.slice(build.indexOf('html: "Dashboard/DashboardPro.html"'), build.indexOf('html: "OIFlow/oi-flow.html"'));
assert.doesNotMatch(dashboardPageBlock, /PriceChart\/(?:chart-data|chart-renderer|indicator-engine|history-loader|price-chart)\.js/,
  'Dashboard production bundle must not own chart analytics');

console.log('Price chart contracts: 9/9 passed');
