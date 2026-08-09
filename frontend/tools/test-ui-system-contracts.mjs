import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const theme = read('styles/theme.css');
const responsive = read('styles/responsive.css');
const tables = read('styles/tables.css');
const components = read('styles/components.css');
const modal = read('Dashboard/modal-manager.js');
const algo = read('Dashboard/algo-status.js');
const depth = read('Dashboard/chain/chain-depth.js');
const formatters = read('shared/utils/formatters.js');
const chainTemplate = read('Dashboard/chain/chain-template.js');
const paperTrading = read('Dashboard/paper-trading-shared.js');
const strikeReport = read('Dashboard/strike-detail-report-view.js');
const formatterContext = {};
vm.runInNewContext(formatters, formatterContext);
const chartSources = [
  read('Dashboard/DashboardPro.html'),
  read('PriceChart/price-chart-engine.js'), read('Dashboard/chain/chain-dashboard-renderer.js'),
].join('\n').replace(/<!--[\s\S]*?-->/g, '').replace(/\/\*[\s\S]*?\*\//g, '');
const docsDir = path.join(root, '../docs/03_UI_System');
const docs = fs.readdirSync(docsDir).filter((name) => name.endsWith('.md'))
  .map((name) => fs.readFileSync(path.join(docsDir, name), 'utf8'));
const canvases = [...chartSources.matchAll(/<canvas\b[^>]*>/g)].map(([tag]) => tag);

const checks = [
  ['semantic color tokens are centralized', ['--pos:', '--neg:', '--warn:', '--info:', '--disabled:'].every((token) => theme.includes(token))],
  ['typography uses shared roles and tabular numerics', theme.includes('--fs-3xl:') && theme.includes('--font-mono:') && tables.includes('font-variant-numeric:tabular-nums')],
  ['compact layout starts below 1280px without hiding cards', responsive.includes('@media (max-width:1279px)') && responsive.includes('grid-template-columns:1fr')],
  ['dense tables own overflow and sticky headers', tables.includes('overflow-x:auto') && tables.includes('position:sticky')],
  ['reduced motion is global', responsive.includes('@media (prefers-reduced-motion:reduce)') && responsive.includes('scroll-behavior:auto!important')],
  ['modal focus is trapped and restored', modal.includes('trapFocus') || (modal.includes('focusableSelector') && modal.includes('requestAnimationFrame(() => invoker.focus())'))],
  ['live popover actions are native labeled buttons', algo.includes('<button type="button" class="algo-close"') && depth.includes('<button type="button" class="depth-reset"')],
  ['all shipped canvases expose an accessible chart question', canvases.length > 0 && canvases.every((tag) => /role="img"/.test(tag) && /aria-label="[^"]+"/.test(tag))],
  ['every UI-system document records implementation status', docs.length === 10 && docs.every((doc) => doc.includes('## Implementation status'))],
  ['focus treatment uses a semantic token', components.includes('.depth-reset:focus-visible') && components.includes('var(--info)')],
  ['HTML escaping has one shared implementation', formatterContext.escapeHtml(`<b title="x">'&`) === '&lt;b title=&quot;x&quot;&gt;&#39;&amp;' && !chainTemplate.includes('const escapeHtml =') && paperTrading.includes('return escapeHtml(s)') && strikeReport.includes('return escapeHtml(v)')],
  ['Indian compact totals use one shared formatter', formatterContext.fmtCrLK(12_500_000) === '1.25Cr' && formatterContext.fmtCrLK(-250_000) === '-2.50L' && !chainTemplate.includes('const fmtCrLK =')],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) failed++;
}
if (failed) process.exit(1);
console.log(`\n${checks.length}/${checks.length} UI-system checks passed.`);
