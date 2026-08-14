// ============================================================
// scenario-analysis-view.js
// "Scenario Analysis" — standalone Confirmation-zone card answering one
// question: what does this position return across a range of spot moves.
//
// IA redesign step 7 (dashboard-redesign-proposal.md §2.3/§5): third
// sub-card pulled out of Advanced Analytics' single collapsed
// "misc analytics" wrapper into its own purpose-specific card. Content
// and behavior are unchanged from the old _aaScenarioPnlHtml sub-card
// (advanced-analytics-view.js) — only the wrapper changed, from a grid
// cell inside #advanced-analytics-card to its own top-level
// <details class="card">, same treatment volatility-view.js and
// probability-view.js already got.
//
// Must load after: formatters.js (fmtI/fmtN), chain-helpers.js
// (activeAtm), chain/chain-view.js (ChainView). Must load before
// dashboard.js.
// ============================================================

// Net-new: no existing card models P&L directly. Kept deliberately
// simple and transparent — a long ATM straddle's INTRINSIC value at
// expiry across a handful of spot-move scenarios, vs the premium paid
// today (d.callPremium + d.putPremium). This is expiry P&L, not a live
// mark-to-market re-price (that would need a full Black-Scholes repricing
// per scenario, which the Institutional F&O Simulator's GEX view already
// approximates for gamma exposure — this card answers a different
// question: "what does this position return if held to expiry").
//
// Shared by both the table and the bar chart below, so the two never
// drift out of sync with each other.
const SCEN_PNL_MOVES = [-0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03];
function _scenPnlData(d) {
  const spot = d.spot || 0;
  const atm = activeAtm(d);
  const straddle = (d.callPremium || 0) + (d.putPremium || 0);
  if (!spot || !straddle) return null;
  const scenarios = SCEN_PNL_MOVES.map(m => {
    const scenSpot = spot * (1 + m);
    const intrinsic = Math.max(scenSpot - atm, 0) + Math.max(atm - scenSpot, 0);
    return { move: m, scenSpot, pnl: intrinsic - straddle };
  });
  return { spot, atm, straddle, scenarios };
}

function _scenPnlHtml(d) {
  const data = _scenPnlData(d);
  if (!data) return `<div class="dd-empty">No premium data yet.</div>`;
  const { atm, straddle, scenarios } = data;

  const rows = scenarios.map(({ move: m, scenSpot, pnl }) => {
    const isBase = m === 0;
    return `<tr>
      <td class="${isBase ? 'atm-sc' : 'sc'}">${m > 0 ? '+' : ''}${(m * 100).toFixed(0)}%</td>
      <td style="font-family:var(--mono);">${fmtI(scenSpot)}</td>
      <td style="font-family:var(--mono);color:${pnl >= 0 ? 'var(--green)' : 'var(--red)'};">${pnl >= 0 ? '+' : '\u2212'}\u20b9${fmtN(Math.abs(pnl), 2)}</td>
    </tr>`;
  }).join('');

  // Bar chart sits above the table for a faster visual read (red/green
  // by move); the table stays underneath for anyone who wants the exact
  // per-scenario numbers. Canvas is recreated every time this card's
  // outerHTML gets rebuilt (patchOuterHtmlIfChanged / full renderDashboard
  // pass), so the Chart.js instance itself is created/rebound lazily —
  // see _ensureScenarioPnlChart below, same pattern as ensureGreeksChart
  // in chart-legend.js. window.updateScenarioPnlChart(d) must be called
  // by whoever puts this HTML into the DOM (chain-dashboard-renderer.js /
  // chain-analytics-renderer.js) since the canvas doesn't exist yet at
  // the point this string is built.
  return `
    <div class="scenario-pnl-chart-wrap">
      <canvas id="scenarioPnlChart" role="img" aria-label="Bar chart of long ATM straddle profit and loss per option unit across spot moves from -3% to +3%, green bars for gains and red bars for losses."></canvas>
    </div>
    <div class="legend-foot" style="margin-bottom:7px;">
      Assumptions: ATM ${fmtI(atm)} · entry premium ₹${fmtN(straddle,2)} per option unit · held to expiry · intrinsic payoff only · excludes lot multiplier, brokerage, taxes, fees and slippage.
    </div>
    <table class="t"><thead><tr><th>Spot Move</th><th>Scenario Spot</th><th>P&amp;L / unit (gross)</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ── Chart.js bar chart ──────────────────────────────────────────────────
// Lazily created/rebound per canvas id, exactly like ensureGreeksChart in
// chart-legend.js — necessary because #scenario-analysis-card's outerHTML
// (and therefore its <canvas>) gets replaced on every tick the scenario
// numbers change (patchOuterHtmlIfChanged in chain-analytics-renderer.js),
// not updated in place.
let _scenPnlChart = null;
let _scenPnlChartSig = null;

function _ensureScenarioPnlChart(canvasId) {
  const canvasEl = document.getElementById(canvasId || 'scenarioPnlChart');
  if (!canvasEl) return null;
  if (_scenPnlChart && _scenPnlChart.canvas === canvasEl) return _scenPnlChart;
  if (_scenPnlChart) { try { _scenPnlChart.destroy(); } catch (e) {} }

  _scenPnlChart = new Chart(canvasEl, {
    type: 'bar',
    data: {
      labels: [],
      datasets: [{
        label: 'P&L / unit (gross)',
        data: [],
        backgroundColor: [],
        borderRadius: 3,
        maxBarThickness: 40
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => items.length ? `Spot move ${items[0].label}` : '',
            label: (item) => {
              const v = item.raw;
              return `P&L: ${v >= 0 ? '+' : '\u2212'}\u20b9${Math.abs(v).toFixed(2)} / unit`;
            }
          }
        }
      },
      scales: {
        y: {
          ticks: { color: '#898781', callback: (v) => (v > 0 ? '+' : '') + v },
          grid: { color: '#e1e0d9' }
        },
        x: { ticks: { color: '#898781' }, grid: { display: false } }
      }
    }
  });
  return _scenPnlChart;
}

// Call after the scenario-analysis-card DOM lands — see hook points in
// ChainView.prototype.renderDashboard (full rebuild) and
// _rerenderChainPanels (incremental patch) in chain-dashboard-renderer.js
// / chain-analytics-renderer.js. Skips redundant Chart.js work when the
// underlying scenario inputs haven't moved.
window.updateScenarioPnlChart = function(d, force) {
  const chart = _ensureScenarioPnlChart('scenarioPnlChart');
  if (!chart) return;
  const data = _scenPnlData(d);
  if (!data) return;

  const sig = `${data.spot}|${data.atm}|${data.straddle}`;
  if (!force && sig === _scenPnlChartSig) return;
  _scenPnlChartSig = sig;

  const posColor = (getComputedStyle(document.documentElement).getPropertyValue('--pos') || '#20C997').trim();
  const negColor = (getComputedStyle(document.documentElement).getPropertyValue('--neg') || '#FF6B6B').trim();

  chart.data.labels = data.scenarios.map(({ move: m }) => (m > 0 ? '+' : '') + (m * 100).toFixed(0) + '%');
  chart.data.datasets[0].data = data.scenarios.map(s => s.pnl);
  chart.data.datasets[0].backgroundColor = data.scenarios.map(s => s.pnl >= 0 ? posColor : negColor);
  chart.update('none'); // 'none' = no re-animation on every tick
};

// Scenario Analysis is a collapsed-by-default <details class="card">
// (unlike the always-visible Greeks by Moneyness card), so the canvas
// gets created while its parent is display:none and Chart.js measures a
// 0×0 box on that first draw. A 'toggle' listener catches the moment the
// card actually opens and forces a resize + redraw against real
// dimensions — same fix shape as modal-manager.js's explicit
// resizeGreeksMoneynessChart('greeksChart-modal') call on modal-open,
// just triggered by <details> opening instead of a modal. Must be
// (re)bound every time the card's outerHTML is rebuilt, since the swap
// replaces the element and drops its listeners — call this right after
// window.updateScenarioPnlChart at both DOM-landing hook points.
window.bindScenarioPnlChartToggle = function() {
  const el = document.getElementById('scenario-analysis-card');
  if (!el || el.dataset.pnlToggleBound) return;
  el.dataset.pnlToggleBound = '1';
  el.addEventListener('toggle', () => {
    if (el.hasAttribute('open') && _scenPnlChart) {
      _scenPnlChart.resize();
      _scenPnlChart.update('none');
    }
  });
};

// ── top-level wrapper ──
// Collapsed by default, matching every other Tier-3 <details class="card">
// in the Confirmation zone (Volatility, Probability, Advanced Analytics,
// Strategy Payoff / Institutional F&O Simulator) — one question, one
// card, opened after the Tier-1 verdict rather than competing with it
// for always-visible space (§3 of the IA redesign proposal).
ChainView.prototype.buildScenarioAnalysisHtml = function(d) {
  return `
  <details class="card" id="scenario-analysis-card">
    <summary>
      <div class="card-head"><span class="ic">\ud83c\udfaf</span>Scenario Analysis<span class="fill"></span></div>
      <span class="chev">\u25b6</span>
    </summary>
    <div class="detail-body">
      <div class="section-card sc-neutral" style="min-width:0;">
        <div class="section-header">
          <span class="section-title"><span class="section-icon">\ud83c\udfaf</span>Scenario P&amp;L</span>
        </div>
        <div style="padding:2px 0;">${_scenPnlHtml(d)}</div>
        <div class="legend-foot" style="margin-top:6px;">Scenario estimate only — long ATM straddle payoff if held to expiry; not a live mark-to-market price or expected return.</div>
      </div>
    </div>
  </details>`;
};