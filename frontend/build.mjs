// build.mjs
// Bundles this project's classic (non-module) <script> files into one
// minified JS file per page, and each page's <link rel=stylesheet> files
// into one minified CSS file per page — without changing execution order
// or behavior.
//
// Why concatenation instead of esbuild's --bundle / import-graph mode:
// every file here is a plain global-scope script (class/const/function
// declared at top level, referenced elsewhere as a bare identifier —
// e.g. `new MarketStore()`, `AppState.wsState`), not an ES module with
// import/export. Several files' own header comments spell out hard
// load-order requirements (e.g. ws-manager.js before market-store.js
// before data-service.js). Running esbuild's module bundler on these
// would put each file in its own module scope and break every one of
// those bare cross-file references. Concatenating in the exact order
// the HTML already loads them in, then minifying the result, keeps
// identical runtime behavior while still cutting the request count and
// shipping smaller code.
//
// External resources (CDN Chart.js, Google Fonts) are left as separate
// tags — they're already off the origin server and shouldn't be bundled.

import { transform } from "esbuild";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

const root = import.meta.dirname;
const outDir = path.join(root, "dist");

// ---- Page definitions: exact on-disk paths, in HTML load order --------
// Each entry mirrors what's currently in the page's <script src> /
// <link href> tags. A "chart-cdn" marker splits the JS list where the
// external Chart.js <script> tag currently sits, so we emit two local
// bundles with the CDN tag preserved between them.
const pages = [
  {
    html: "Dashboard/DashboardPro.html",
    out: "dashboard",
    css: [
      "styles/theme.css", "styles/animations.css", "styles/layout.css", "styles/tables.css",
      "styles/navigation.css", "styles/panels.css", "PriceChart/price-chart-components.css",
      "styles/paper-trading.css", "styles/algo-status.css", "styles/responsive.css", "styles/fiidii-report.css",
      "PriceChart/pc-order-panel.css",
    ],
    js: [
      ["shared/config.js", "shared/logger.js", "shared/state/app-state.js", "shared/utils/event-bus.js"],
      // -- external Chart.js CDN script stays here, unbundled --
      [
        "Dashboard/chart-legend.js",
        "PriceChart/chart-data.js", "PriceChart/chart-renderer.js",
        "PriceChart/indicator-engine.js", "PriceChart/history-loader.js",
        "PriceChart/price-chart.js",
        "shared/utils/formatters.js", "shared/utils/dom-utils.js", "Dashboard/range-tabs.js",
        "Dashboard/chain/chain-helpers.js", "Dashboard/chain/chain-view.js", "Dashboard/chain/chain-template.js",
        "Dashboard/chain/chain-view-models.js",
        "Dashboard/conviction-gauge.js",
        "Dashboard/chain/chain-renderer.js", "Dashboard/chain/chain-depth.js", "Dashboard/chain/chain-greeks.js",
        "Dashboard/chain/chain-sync.js",
        "engines/smart-money.js", "engines/market-structure.js",
        "Dashboard/dashboard-thresholds.js", "Dashboard/strike-detail-report-panel.js",
        "Dashboard/oi-flow-view.js", "Dashboard/exec-view.js", "Dashboard/strategy-view.js",
        "Dashboard/simulator-view.js",
        "Dashboard/advanced-analytics-view.js",
        "Dashboard/modal-manager.js",
        "Dashboard/fiidii-report.js",
        "shared/services/ws-manager.js", "shared/stores/market-store.js", "shared/services/data-service.js",
        "Dashboard/ui-controls.js", "Dashboard/panel-manager.js", "Dashboard/dashboard-panels.js",
        "Dashboard/dashboard.js", "Dashboard/paper-trading.js", "Dashboard/algo-status.js",
      ],
    ],
  },
  {
    html: "OIFlow/oi-flow.html",
    out: "oi-flow",
    css: ["styles/theme.css", "OIFlow/oi-flow.css"],
    js: [["shared/config.js", "shared/logger.js", "OIFlow/oi-flow.js"]],
  },
  {
    html: "OptionChain/option-chain.html",
    out: "option-chain",
    css: ["styles/theme.css", "OptionChain/option-chain.css"],
    js: [["engines/smart-money.js", "engines/market-structure.js", "OptionChain/option-chain.js"]],
  },
  {
    html: "PriceChart/price-chart.html",
    out: "price-chart",
    css: [
      "styles/theme.css",
      "PriceChart/price-chart-components.css", "PriceChart/pc-order-panel.css",
      "PriceChart/price-chart-standalone.css",
    ],
    js: [[
      "shared/config.js", "shared/logger.js",
      "shared/state/app-state.js", "shared/utils/event-bus.js", "shared/utils/dom-utils.js", "shared/utils/formatters.js",
      "shared/services/ws-manager.js", "shared/stores/market-store.js",
      "PriceChart/chart-data.js", "PriceChart/chart-renderer.js",
      "PriceChart/indicator-engine.js", "PriceChart/history-loader.js",
      "PriceChart/price-chart.js", "PriceChart/price-chart-standalone.js",
    ]],
  },
];

// ---- Standalone pages: not bundled themselves (no <script>/<link> list
// of their own to concatenate), but still need to ship in dist/ pointing
// at a bundle that already exists there. Each entry rewrites its literal
// <link href="..."> tags to the given page's single bundled CSS output
// instead, then writes the result into that same page's dist output dir
// — everything else in the file (markup, inline <style>/<script>) is
// copied byte-for-byte. Currently empty — strike-detail-report.html (the
// one page that used to live here) was replaced by an in-page modal
// inside DashboardPro.html, so it no longer needs its own dist copy or
// CSS-link rewrite. Kept as an array (not removed outright) so a future
// standalone popup page has somewhere to register.
const standalonePages = [];

async function concatCSS(files) {
  const parts = await Promise.all(
    files.map(async (f) => `/* ---- ${f} ---- */\n` + (await readFile(path.join(root, f), "utf8")))
  );
  return parts.join("\n");
}

async function concatJS(files) {
  const parts = await Promise.all(
    files.map(async (f) => `// ---- ${f} ----\n` + (await readFile(path.join(root, f), "utf8")))
  );
  return parts.join("\n;\n");
}

async function main() {
  await mkdir(outDir, { recursive: true });
  const report = [];

  for (const page of pages) {
    const pageOutDir = path.join(outDir, path.dirname(page.html));
    await mkdir(pageOutDir, { recursive: true });

    // --- CSS: concat then minify as one file ---
    const cssSrc = await concatCSS(page.css);
    const cssMin = await transform(cssSrc, { loader: "css", minify: true });
    const cssOutName = `${page.out}.bundle.css`;
    await writeFile(path.join(pageOutDir, cssOutName), cssMin.code);

    // --- JS: one minified bundle per chunk (split around external CDN scripts) ---
    const jsOutNames = [];
    for (let i = 0; i < page.js.length; i++) {
      const src = await concatJS(page.js[i]);
      // format: "iife" would wrap in a function and create a new scope,
      // which breaks the bare-global-reference pattern these files use.
      // We only want minification/whitespace removal, not scoping changes,
      // so we transform as plain script (no format wrapper).
      const min = await transform(src, { loader: "js", minify: true });
      const name = `${page.out}.bundle.${i + 1}.js`;
      await writeFile(path.join(pageOutDir, name), min.code);
      jsOutNames.push(name);
    }

    report.push({ page: page.html, css: cssOutName, js: jsOutNames });
  }

  // --- Standalone pages: copy into dist, rewriting CSS links to the
  // matching page's bundle output (see standalonePages comment above) ---
  for (const sp of standalonePages) {
    const srcHtml = await readFile(path.join(root, sp.html), "utf8");
    const targetPage = pages.find((p) => p.out === sp.bundleOf);
    if (!targetPage) {
      throw new Error(`standalonePages: no page with out="${sp.bundleOf}" for ${sp.html}`);
    }
    const bundledLink = `<link rel="stylesheet" href="${sp.bundleOf}.bundle.css">`;
    let outHtml = srcHtml;
    for (const oldLinks of sp.linksToReplace) {
      if (!outHtml.includes(oldLinks)) {
        throw new Error(`standalonePages: expected link block not found in ${sp.html} — did the source file change? Expected:\n${oldLinks}`);
      }
      outHtml = outHtml.replace(oldLinks, bundledLink);
    }
    const spOutDir = path.join(outDir, path.dirname(sp.html));
    await mkdir(spOutDir, { recursive: true });
    await writeFile(path.join(spOutDir, path.basename(sp.html)), outHtml);
    report.push({ page: sp.html, rewrittenTo: bundledLink });
  }

  console.log(JSON.stringify(report, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
