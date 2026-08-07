import { readFile } from 'node:fs/promises';

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8');
const [oiHtml, oiJs, oiCss, backtest, responsive, priceCss] = await Promise.all([
  read('OIFlow/oi-flow.html'), read('OIFlow/oi-flow.js'), read('OIFlow/oi-flow.css'),
  read('Dashboard/backtest-view.js'), read('styles/responsive.css'),
  read('PriceChart/price-chart-standalone.css'),
]);

const checks = [
  ['OI Flow controls use native buttons', !/<div class="(?:tab|rng-tab|win-tab|mode-tab)"/.test(oiHtml)],
  ['OI Flow exposes pressed state', /setAttribute\('aria-pressed'/.test(oiJs)],
  ['OI Flow has visible keyboard focus', /\.tab:focus-visible/.test(oiCss)],
  ['OI Flow has compact layout', /@media \(max-width:1279px\)/.test(oiCss)],
  ['Backtest uses shared modal behavior', /app\.modal\._openModal/.test(backtest) && /app\.modal\._closeModal/.test(backtest)],
  ['Backtest close is a named button', /<button[^>]+class="bt-close"[^>]+aria-label="Close backtest"/.test(backtest)],
  ['Dashboard honors reduced motion', /prefers-reduced-motion:reduce/.test(responsive)],
  ['Price Chart has compact layout', /@media \(max-width:1279px\)/.test(priceCss)],
];

let failed = 0;
for (const [name, pass] of checks) {
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}`);
  if (!pass) failed++;
}
console.log(`\n${checks.length - failed}/${checks.length} PDS-00 design-system checks passed.`);
if (failed) process.exit(1);
