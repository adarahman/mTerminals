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
const orderCss = fs.readFileSync(new URL('../PriceChart/pc-order-panel.css', import.meta.url), 'utf8');

assert.match(dashboard, /onclick="openPriceChartModal\(\)"/,
  'dashboard mini chart must open the native Price Chart modal');
assert.match(dashboardHtml, /id="price-chart-modal"[\s\S]*id="price-chart-modal-host"/,
  'DashboardPro must own the Price Chart modal and mount host');
assert.match(dashboardHtml, /<div class="oc-modal-id">[\s\S]*<span class="oc-modal-title">📈 Price Chart<\/span>\s*<\/div>\s*<div id="price-chart-order-host"/,
  'quick-order controls must occupy the modal header beside the Price Chart title');
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
assert.match(chart, /id="pc-order-side"[\s\S]*aria-pressed="false">BUY<\/button>[\s\S]*id="pc-order-opt"[\s\S]*aria-pressed="false">CE<\/button>/,
  'quick-order side and option type must each be a single compact toggle');
assert.match(chart, /id="pc-order-type"[\s\S]*data-order-type="LIMIT"[\s\S]*aria-pressed="false">LIMIT<\/button>/,
  'quick-order LIMIT/MARKET type must be a single compact toggle');
assert.match(chart, /orderType\.dataset\.orderType = isMarket \? 'MARKET' : 'LIMIT'[\s\S]*priceInput\.disabled = isMarket/,
  'order type toggle must update selection and disable limit price for market orders');
assert.match(chart, /orderType: orderType\.dataset\.orderType,[\s\S]*price: orderType\.dataset\.orderType === 'MARKET' \? null/,
  'submitted payload must use the selected LIMIT/MARKET type');
assert.match(chart, /host\.querySelector\('#pc-order-panel'\)[\s\S]*orderHost\.replaceChildren\(orderPanel\)/,
  'quick-order controls must be moved from the mounted chart into the modal title row');
assert.match(chart, /id="pc-settings-toggle"[\s\S]*<\/div>\s*<div hidden>\$\{this\._orderPanelHtml\(\)\}<\/div>/,
  'quick-order controls must be staged outside visible chart controls before header relocation');
assert.match(chart, /this\._orderState\.side = this\._orderState\.side === 'BUY' \? 'SELL' : 'BUY'[\s\S]*this\._orderState\.opt = this\._orderState\.opt === 'CALL' \? 'PUT' : 'CALL'/,
  'quick-order toggles must switch between buy/sell and CE/PE');
assert.doesNotMatch(chart, /pc-order-sltgt|>SL\/TGT</,
  'unused SL/TGT control must stay removed');
assert.doesNotMatch(chart, /pc-order-symbol|pc-order-chg/,
  'contract strike/type/expiry must not be duplicated outside the strike selector');
assert.match(chart, /selectedOption\.textContent = `\$\{label\} · \$\{fmtI\(contract\.strike\)\} · \$\{contract\.expiry\}`/,
  'strike selector must show the selected strike and expiry without repeating CE/PE');
assert.match(orderCss, /\.pc-order-body\{[^}]*flex-wrap:wrap/,
  'order controls must wrap instead of clipping fields when the header is narrow');
assert.match(orderCss, /\.pc-order-body select\{[^}]*min-width:64px/,
  'order selects must retain a usable minimum width');
assert.match(orderCss, /\.price-chart-order-host\{[^}]*flex:1 1 500px/,
  'order controls must receive the remaining width of the outer modal header');

console.log('Price chart contracts: 24/24 passed');
