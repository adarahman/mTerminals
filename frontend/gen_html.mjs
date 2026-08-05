import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

const root = import.meta.dirname;
const outDir = path.join(root, "dist");

const jobs = [
  {
    src: "Dashboard/DashboardPro.html", out: "Dashboard/DashboardPro.html",
    cssBlock: /<link rel="stylesheet" href="\.\.\/styles\/theme\.css">[\s\S]*?<link rel="stylesheet" href="\.\.\/PriceChart\/pc-order-panel\.css">/,
    cssReplacement: `<link rel="stylesheet" href="dashboard.bundle.css">`,
    jsBlock: /<script src="\.\.\/shared\/config\.js"><\/script>[\s\S]*?<script src="backtest-view\.js"><\/script>/,
    jsReplacement:
`<script src="dashboard.bundle.1.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
    <script src="dashboard.bundle.2.js"></script>`,
    // remove the now-duplicated original CDN line (it's inside jsBlock originally between the two)
  },
  {
    src: "OIFlow/oi-flow.html", out: "OIFlow/oi-flow.html",
    cssBlock: /<link rel="stylesheet" href="\.\.\/styles\/theme\.css">\s*<link rel="stylesheet" href="oi-flow\.css">/,
    cssReplacement: `<link rel="stylesheet" href="oi-flow.bundle.css">`,
    jsBlock: /<script src="\.\.\/shared\/config\.js"><\/script>\s*<script src="\.\.\/shared\/logger\.js"><\/script>\s*<script src="oi-flow\.js"><\/script>/,
    jsReplacement: `<script src="oi-flow.bundle.1.js"></script>`,
  },
  {
    src: "OptionChain/option-chain.html", out: "OptionChain/option-chain.html",
    cssBlock: /<link rel="stylesheet" href="\.\.\/styles\/theme\.css">[\s\S]*?<link rel="stylesheet" href="option-chain\.css">/,
    cssReplacement: `<link rel="stylesheet" href="option-chain.bundle.css">`,
    jsBlock: /<script src="\.\.\/engines\/smart-money\.js"><\/script>\s*<script src="\.\.\/engines\/market-structure\.js"><\/script>\s*<script src="option-chain\.js"><\/script>/,
    jsReplacement: `<script src="option-chain.bundle.1.js"></script>`,
  },
  {
    src: "PriceChart/price-chart.html", out: "PriceChart/price-chart.html",
    cssBlock: /<link rel="stylesheet" href="\.\.\/styles\/theme\.css">\s*<link rel="stylesheet" href="price-chart-components\.css">\s*<link rel="stylesheet" href="pc-order-panel\.css">\s*<link rel="stylesheet" href="price-chart-standalone\.css">/,
    cssReplacement: `<link rel="stylesheet" href="price-chart.bundle.css">`,
    jsBlock: /<script src="\.\.\/shared\/config\.js"><\/script>[\s\S]*?<script src="price-chart-standalone\.js"><\/script>/,
    jsReplacement: `<script src="price-chart.bundle.1.js"></script>`,
  },
];

for (const job of jobs) {
  let html = await readFile(path.join(root, job.src), "utf8");
  if (!job.cssBlock.test(html)) throw new Error(`CSS block not matched for ${job.src}`);
  html = html.replace(job.cssBlock, job.cssReplacement);
  if (!job.jsBlock.test(html)) throw new Error(`JS block not matched for ${job.src}`);
  html = html.replace(job.jsBlock, job.jsReplacement);
  const outPath = path.join(outDir, job.out);
  await mkdir(path.dirname(outPath), { recursive: true });
  await writeFile(outPath, html);
  console.log("wrote", outPath);
}
