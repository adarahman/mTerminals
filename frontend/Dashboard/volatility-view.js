// ============================================================
// volatility-view.js
// "Volatility" — standalone Confirmation-zone card answering one
// question: is IV rich or cheap right now, and relative to what.
//
// IA redesign step 7 (dashboard-redesign-proposal.md §2.3/§5): first
// sub-card pulled out of Advanced Analytics' single collapsed
// "misc analytics" wrapper into its own purpose-specific card. Content
// and behavior are unchanged from the old _aaIvRankHtml sub-card
// (advanced-analytics-view.js) — only the wrapper changed, from a grid
// cell inside #advanced-analytics-card to its own top-level
// <details class="card">, so it can render, collapse, and (in a later
// step 7 pass) hold future volatility content independently of the
// remaining Advanced Analytics sub-cards.
//
// Must load after: formatters.js (fmtN), chain/chain-view.js (ChainView).
// Must load before: chain/chain-renderer.js's incremental-refresh pass
// runs (i.e. before any render call — script tag order in
// DashboardPro.html satisfies this same as advanced-analytics-view.js).
// ============================================================

// Distilled metric strip: same fields, same "Full Surface →" destination
// as the old standalone IV/HV/Skew Tier-3 card (removed 2026-08-01, see
// chain-template.js's history comment) — this card's job is the raw
// numbers, not alerts (those live inline on the always-visible Greeks /
// Net GEX card).
function _volIvRankHtml(d) {
  const rank = d.ivRank || 0;
  const ivVsHv = (d.atmIV || 0) - (d.hv30 || 0);
  const richCheap = ivVsHv >= 0 ? 'rich' : 'cheap';
  return `<div class="metric-strip">
      <div class="metric-cell"><div class="k">IV Rank</div><div class="v">${Math.round(rank)}/100</div></div>
      <div class="metric-cell"><div class="k">ATM IV</div><div class="v">${fmtN(d.atmIV, 2)}%</div></div>
      <div class="metric-cell"><div class="k">HV (30d)</div><div class="v">${fmtN(d.hv30, 2)}%</div></div>
      <div class="metric-cell"><div class="k">IV vs HV</div><div class="v ${ivVsHv >= 0 ? 'bear' : 'bull'}">${fmtN(ivVsHv, 2)}% ${richCheap}</div></div>
      <div class="metric-cell"><div class="k">Skew</div><div class="v">${fmtN(d.atmSkew, 2)}%</div></div>
    </div>`;
}

// ── ATM IV Term Structure ───────────────────────────────────────────────
// Net-new: plots ATM IV across the expiries the backend actually has data
// for — the primary expiry plus whatever extra_chains bundles came
// through (currently NEAR + MONTHLY; see option_chain_json.py's `slots`
// list and mTerminals_json.py's chains_by_expiry/__meta__ build). That's
// the exact same "has data" set the expiry <select> already distinguishes
// via its ● vs ○ bullets (renderExpiryOptions in chain-dense-renderer.js)
// — this chart never invents a point for an ○ expiry the backend hasn't
// actually fetched IV for.
//
// One line, x = DTE, y = ATM IV. A rising line into further expiries
// (contango) vs falling (backwardation) is the whole point — it's a
// calendar-spread read, not a bull/bear signal, so no pos/neg coloring.
function _ivTermStructureData(d) {
  const points = [];
  const seen = new Set();
  if (d.expiry && d.dte != null && d.atmIV != null) {
    points.push({ expiry: d.expiry, dte: Number(d.dte), atmIV: Number(d.atmIV) });
    seen.add(d.expiry);
  }
  const metaStore = d.chainMeta || {};
  Object.keys(metaStore).forEach(exp => {
    if (seen.has(exp)) return;
    const meta = metaStore[exp];
    if (!meta || meta.atmIV == null || meta.dte == null) return;
    points.push({ expiry: exp, dte: Number(meta.dte), atmIV: Number(meta.atmIV) });
    seen.add(exp);
  });
  points.sort((a, b) => a.dte - b.dte);
  return points;
}

function _ivTermStructureHtml(d) {
  const points = _ivTermStructureData(d);
  // Fewer than 2 expiries with IV data (e.g. --no-extra-chains, or both
  // extra bundles failed to fetch) means there's nothing to draw a line
  // between — say so plainly instead of rendering an empty/1-dot canvas.
  if (points.length < 2) {
    return `<div class="dd-empty">Need at least two expiries with data to plot a term structure — only ${points.length} available right now.</div>`;
  }
  return `
    <div class="iv-term-chart-wrap">
      <canvas id="ivTermStructureChart" role="img" aria-label="Line chart of at-the-money implied volatility across available expiries, x-axis days to expiry, y-axis ATM IV percent."></canvas>
    </div>
    <div class="legend-foot" style="margin-top:6px;">ATM IV per expiry the backend currently has data for (primary + NEAR/MONTHLY) — rising into later expiries is contango, falling is backwardation.</div>`;
}

let _ivTermChart = null;
let _ivTermChartSig = null;

function _ensureIvTermChart(canvasId) {
  const canvasEl = document.getElementById(canvasId || 'ivTermStructureChart');
  if (!canvasEl) return null;
  if (_ivTermChart && _ivTermChart.canvas === canvasEl) return _ivTermChart;
  if (_ivTermChart) { try { _ivTermChart.destroy(); } catch (e) {} }

  _ivTermChart = new Chart(canvasEl, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'ATM IV',
        data: [],
        borderColor: '#eda100',
        backgroundColor: '#eda100',
        borderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6,
        tension: 0.25,
        fill: false
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => items.length ? items[0].label : '',
            label: (item) => `ATM IV: ${item.raw.toFixed(2)}%`
          }
        }
      },
      scales: {
        y: {
          ticks: { color: '#898781', callback: (v) => v + '%' },
          grid: { color: '#e1e0d9' }
        },
        x: { ticks: { color: '#898781' }, grid: { display: false } }
      }
    }
  });
  return _ivTermChart;
}

// Call after the volatility-card DOM lands — same hook shape as
// window.updateScenarioPnlChart in scenario-analysis-view.js. Skips the
// redraw when the underlying expiry/IV set hasn't moved.
window.updateIvTermChart = function(d, force) {
  const points = _ivTermStructureData(d);
  if (points.length < 2) return; // canvas doesn't exist in this case — see _ivTermStructureHtml
  const chart = _ensureIvTermChart('ivTermStructureChart');
  if (!chart) return;

  const sig = points.map(p => `${p.expiry}:${p.dte}:${p.atmIV}`).join('|');
  if (!force && sig === _ivTermChartSig) return;
  _ivTermChartSig = sig;

  chart.data.labels = points.map(p => [p.expiry, `${p.dte}d`]);
  chart.data.datasets[0].data = points.map(p => p.atmIV);
  chart.update('none');
};

// Volatility card is a collapsed-by-default <details> (same as Scenario
// Analysis), so the canvas is measured at 0×0 the first time it draws
// while display:none — bind a 'toggle' listener to force a resize+redraw
// the moment the card actually opens. Must be (re)bound every time the
// card's outerHTML is rebuilt, since the swap drops previously-bound
// listeners — call this right after window.updateIvTermChart at both DOM-
// landing hook points (see scenario-analysis-view.js's
// bindScenarioPnlChartToggle for the identical pattern, including the
// bug of forgetting to actually call it — don't repeat that here).
window.bindIvTermChartToggle = function() {
  const el = document.getElementById('volatility-card');
  if (!el || el.dataset.ivTermToggleBound) return;
  el.dataset.ivTermToggleBound = '1';
  el.addEventListener('toggle', () => {
    if (el.hasAttribute('open') && _ivTermChart) {
      _ivTermChart.resize();
      _ivTermChart.update('none');
    }
  });
};

// ── top-level wrapper ──
// Collapsed by default, matching every other Tier-3 <details class="card">
// in the Confirmation zone (Advanced Analytics, Strategy Payoff /
// Institutional F&O Simulator) — one question, one card, opened after
// the Tier-1 verdict rather than competing with it for always-visible
// space (§3 of the IA redesign proposal).
ChainView.prototype.buildVolatilityHtml = function(d) {
  return `
  <details class="card" id="volatility-card">
    <summary>
      <div class="card-head"><span class="ic">📉</span>Volatility<span class="fill"></span></div>
      <span class="chev">▶</span>
    </summary>
    <div class="detail-body">
      <div class="section-card sc-neutral" style="min-width:0;">
        <button class="section-header nav-card-header" onclick="openIvSurfaceModal()"
           aria-label="Open IV Rank Details — view full surface" title="Open full IV surface">
          <span class="section-title nav-card-header-label"><span class="section-icon">📉</span>IV Rank Details</span>
          <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
        </button>
        <div style="padding:2px 0;">${_volIvRankHtml(d)}</div>
      </div>
    </div>
  </details>`;
};