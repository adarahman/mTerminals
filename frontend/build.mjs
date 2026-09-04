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
import {
  readFile,
  writeFile,
  mkdir,
  rm,
  copyFile,
} from "node:fs/promises";
import path from "node:path";

const root = import.meta.dirname;
const outDir = path.join(root, "dist");

const pages = [
  {
    html: "Dashboard/DashboardPro.html",
    out: "dashboard",

    // Static files required by the generated Dashboard HTML.
    assets: [
      "favicon.svg",
    ],

    css: [
      "styles/theme.css",
      "styles/backtest-view.css",
      "styles/components.css",
      "styles/animations.css",
      "styles/layout.css",
      "styles/tables.css",
      "styles/navigation.css",
      "styles/panels.css",
      "styles/paper-trading.css",
      "styles/algo-status.css",
      "styles/responsive.css",
      "PriceChart/price-chart-components.css",
      "PriceChart/pc-order-panel.css",
      "styles/fiidii-report.css",
    ],

    js: [
      [
        "shared/config.js",
        "shared/logger.js",
        "shared/state/app-state.js",
        "shared/utils/event-bus.js",
      ],

      [
        "Dashboard/chart-legend.js",
        "shared/utils/formatters.js",
        "shared/utils/dom-utils.js",
        "Dashboard/range-tabs.js",

        "Dashboard/chain/chain-helpers.js",
        "Dashboard/chain/market-context.js",
        "Dashboard/chain/metrics.js",
        "Dashboard/chain/chain-view.js",
        "Dashboard/chain/chain-template.js",
        "Dashboard/chain/chain-view-models.js",

        "Dashboard/conviction-gauge.js",

        "Dashboard/chain/chain-dense-renderer.js",
        "Dashboard/chain/chain-dashboard-renderer.js",
        "Dashboard/chain/chain-analytics-renderer.js",
        "Dashboard/chain/chain-depth.js",
        "Dashboard/chain/chain-greeks.js",

        "Dashboard/chain/chain-controls.js",

        "engines/smart-money.js",
        "engines/market-structure.js",

        "Dashboard/dashboard-thresholds.js",

        "Dashboard/oi-flow-view.js",
        "Dashboard/exec-view.js",
        "Dashboard/strategy-view.js",
        "Dashboard/simulator-view.js",
        "Dashboard/strike-detail-report-view.js",
        "Dashboard/volatility-view.js",
        "Dashboard/probability-view.js",
        "Dashboard/scenario-analysis-view.js",
        "Dashboard/advanced-analytics-view.js",

        "PriceChart/chart-data.js",
        "PriceChart/chart-renderer.js",
        "PriceChart/indicator-engine.js",
        "PriceChart/history-loader.js",
        "PriceChart/price-chart-engine.js",

        "Dashboard/modal-manager.js",
        "Dashboard/fiidii-report.js",

        "shared/services/ws-manager.js",
        "shared/stores/market-store.js",
        "shared/services/data-service.js",

        "Dashboard/ui-controls.js",
        "Dashboard/panel-manager.js",
        "Dashboard/dashboard-panels.js",
        "Dashboard/dashboard.js",

        "Dashboard/components/mt-button.js",

        "Dashboard/paper-trading-shared.js",
        "Dashboard/order-entry.js",
        "Dashboard/portfolio-tracker.js",
        "Dashboard/algo-status.js",
        "Dashboard/backtest-view.js",
      ],
    ],
  },
];

/**
 * Read and concatenate CSS files.
 */
async function concatCSS(files) {
  const chunks = [];

  for (const file of files) {
    const filePath = path.join(root, file);
    const content = await readFile(filePath, "utf8");

    chunks.push(
      `/* ===== ${file} ===== */\n${content}`,
    );
  }

  return chunks.join("\n\n");
}

/**
 * Read and concatenate JavaScript files.
 *
 * These scripts intentionally remain classic scripts rather than being
 * bundled as ES modules because the application relies on global scope
 * and deterministic execution order.
 */
async function concatJS(files) {
  const chunks = [];

  for (const file of files) {
    const filePath = path.join(root, file);
    const content = await readFile(filePath, "utf8");

    chunks.push(
      `/* ===== ${file} ===== */\n${content}`,
    );
  }

  return chunks.join("\n\n");
}

/**
 * Build one CSS bundle.
 */
async function buildCSS(files, outputFile) {
  const source = await concatCSS(files);

  const result = await transform(source, {
    loader: "css",
    minify: true,
    legalComments: "none",
  });

  await writeFile(outputFile, result.code);

  console.log(`built ${path.relative(root, outputFile)}`);
}

/**
 * Build one JavaScript bundle.
 */
async function buildJS(files, outputFile) {
  const source = await concatJS(files);

  const result = await transform(source, {
    loader: "js",
    minify: true,
    legalComments: "none",
  });

  await writeFile(outputFile, result.code);

  console.log(`built ${path.relative(root, outputFile)}`);
}

/**
 * Copy page-local static assets into the generated page directory.
 *
 * Example:
 *
 *   Dashboard/favicon.svg
 *            ↓
 *   dist/Dashboard/favicon.svg
 */
async function copyPageAssets(page, pageOutDir) {
  for (const asset of page.assets ?? []) {
    const source = path.join(
      root,
      path.dirname(page.html),
      asset,
    );

    const destination = path.join(
      pageOutDir,
      asset,
    );

    await copyFile(source, destination);

    console.log(
      `copied ${path.relative(root, source)} -> ${path.relative(root, destination)}`,
    );
  }
}

/**
 * Main build.
 */
async function main() {
  await mkdir(outDir, { recursive: true });

  /*
   * Remove obsolete output directories from older builds.
   */
  await rm(path.join(outDir, "OptionChain"), {
    recursive: true,
    force: true,
  });

  await rm(path.join(outDir, "OIFlow"), {
    recursive: true,
    force: true,
  });

  await rm(path.join(outDir, "PriceChart"), {
    recursive: true,
    force: true,
  });

  for (const page of pages) {
    const pageOutDir = path.join(
      outDir,
      "Dashboard",
    );

    await mkdir(pageOutDir, {
      recursive: true,
    });

    /*
     * Copy page-local static assets.
     */
    await copyPageAssets(page, pageOutDir);

    /*
     * CSS bundle.
     */
    const cssOutput = path.join(
      pageOutDir,
      `${page.out}.bundle.css`,
    );

    await buildCSS(
      page.css,
      cssOutput,
    );

    /*
     * JavaScript bundles.
     *
     * The nested arrays intentionally produce separate bundles
     * so that the existing execution order is preserved.
     */
    for (let index = 0; index < page.js.length; index += 1) {
      const jsOutput = path.join(
        pageOutDir,
        `${page.out}.bundle.${index + 1}.js`,
      );

      await buildJS(
        page.js[index],
        jsOutput,
      );
    }
  }

  console.log("Frontend build completed successfully.");
}

main().catch((error) => {
  console.error("Frontend build failed.");
  console.error(error);
  process.exit(1);
});