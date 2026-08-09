import { readFile } from 'node:fs/promises';

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8');
const [backtest, responsive, priceCss, algo, depth, components, dashboardHtml, priceJs, chainRenderer] = await Promise.all([
  read('Dashboard/backtest-view.js'), read('styles/responsive.css'),
  read('styles/responsive.css'),
  read('Dashboard/algo-status.js'), read('Dashboard/chain/chain-depth.js'), read('styles/components.css'),
  read('Dashboard/DashboardPro.html'), read('PriceChart/price-chart-engine.js'), read('Dashboard/chain/chain-dashboard-renderer.js'),
]);

const canvasSources = [dashboardHtml, priceJs, chainRenderer].join('\n')
  .replace(/<!--[\s\S]*?-->/g, '').replace(/\/\*[\s\S]*?\*\//g, '');
const unlabeledCanvases = [...canvasSources.matchAll(/<canvas\b[^>]*>/g)]
  .filter(([tag]) => !/role="img"/.test(tag) || !/aria-label="[^"]+"/.test(tag));

const checks = [
  ['Backtest uses shared modal behavior', /app\.modal\._openModal/.test(backtest) && /app\.modal\._closeModal/.test(backtest)],
  ['Backtest close is a named button', /<button[^>]+class="bt-close"[^>]+aria-label="Close backtest"/.test(backtest)],
  ['Dashboard honors reduced motion', /prefers-reduced-motion:reduce/.test(responsive)],
  ['Price Chart has compact layout', /@media \(max-width:1279px\)/.test(priceCss)],
  ['Algo close is a labeled native button', /<button[^>]+class="algo-close"[^>]+aria-label="Close Algo Status panel"/.test(algo)],
  ['Depth reset is keyboard operable', /<button[^>]+class="depth-reset"[^>]+aria-label="Reset Bid Ask depth to ATM"/.test(depth) && /\.depth-reset:focus-visible/.test(components)],
  ['Every shipped canvas has an accessible question and units', unlabeledCanvases.length === 0],
];

let failed = 0;
for (const [name, pass] of checks) {
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}`);
  if (!pass) failed++;
}
console.log(`\n${checks.length - failed}/${checks.length} PDS-00 design-system checks passed.`);
if (failed) process.exit(1);
