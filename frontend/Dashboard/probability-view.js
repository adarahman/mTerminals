// ============================================================
// probability-view.js
// "Probability" — standalone Confirmation-zone card answering one
// question: which strikes show the strongest institutional-grade
// positioning right now.
//
// IA redesign step 7 (dashboard-redesign-proposal.md §2.3/§5): second
// sub-card pulled out of Advanced Analytics' single collapsed
// "misc analytics" wrapper into its own purpose-specific card. Content
// and behavior are unchanged from the old _aaSmartMoneyRankingHtml
// sub-card (advanced-analytics-view.js) — only the wrapper changed, from
// a grid cell inside #advanced-analytics-card to its own top-level
// <details class="card">, same treatment volatility-view.js already got
// for IV Rank details.
//
// Must load after: formatters.js (fmtI/fmtK), chain-helpers.js
// (getFilteredChain), dashboard-thresholds.js (INST_THRESHOLDS),
// engines/smart-money.js (smartMoneyBadge), chain/chain-view.js
// (ChainView). Must load before dashboard.js.
// ============================================================

// Reuses the same isInst/badge heuristic SimulatorView's Institutional
// Activity table already applies per strike (see simulator-view.js's
// rowHtml), but ranks the visible chain by strength of ΔOI among
// institutional-flagged strikes instead of grouping by near/far band —
// a "who's doing the most right now" list rather than a strike ledger.
//
// Scope is getFilteredChain(d) — the user's ±N chain-range selection,
// same scope Range PCR is honest about, not the true full chain (see
// the step-6-audit correction this label already went through:
// PROJECT-ARCHITECTURE.md §13 / dashboard-redesign-proposal.md §4).
function _probSmartMoneyRankingHtml(d) {
  const chain = getFilteredChain(d);
  if (!chain.length) return `<div class="dd-empty">No chain data yet.</div>`;

  const ratios = d.volOiRatios || {};
  const totals = chain.map(r => (r.ceOI || 0) + (r.peOI || 0)).sort((a, b) => a - b);
  const medianOI = totals[Math.floor(totals.length / 2)] || 1;
  const th = INST_THRESHOLDS.near;

  const rows = chain.map(r => {
    const totalOI = (r.ceOI || 0) + (r.peOI || 0);
    const rawRatio = ratios[String(r.strike)];
    const hasRatioData = !!rawRatio;
    const volRatio = hasRatioData ? ((rawRatio.ce || 0) + (rawRatio.pe || 0)) / 2 : 0;
    const isInst = hasRatioData && totalOI > medianOI * th.oiMult && volRatio < th.volRatioMax;
    const dominant = (r.ceOI || 0) >= (r.peOI || 0) ? 'CE' : 'PE';
    const oiDomChg = dominant === 'CE' ? (r.ceChgOI || 0) : (r.peChgOI || 0);
    const badge = smartMoneyBadge(hasRatioData, isInst, oiDomChg, totalOI, volRatio, th);
    return { strike: r.strike, dominant, oiDomChg, badge };
  })
    .filter(row => row.badge.label !== 'RETAIL')
    .sort((a, b) => Math.abs(b.oiDomChg) - Math.abs(a.oiDomChg))
    .slice(0, 8);

  return rows.length
    ? rows.map((row, i) => `
      <div style="display:flex;align-items:center;gap:10px;padding:7px 2px;${i < rows.length - 1 ? 'border-bottom:1px solid var(--border);' : ''}">
        <span>${row.badge.dot}</span>
        <span style="font-family:var(--mono);font-weight:600;flex:0 0 64px;">${fmtI(row.strike)}</span>
        <span style="flex:0 0 26px;color:var(--txt3);font-size:11px;">${row.dominant}</span>
        <span style="flex:1;font-weight:600;font-size:11px;color:${row.badge.color};">${row.badge.label}</span>
        <span style="font-family:var(--mono);color:${row.oiDomChg >= 0 ? 'var(--green)' : 'var(--amber)'};">${row.oiDomChg >= 0 ? '+' : '\u2212'}${fmtK(Math.abs(row.oiDomChg))}</span>
      </div>`).join('')
    : `<div class="dd-empty">No institutional-grade activity flagged right now.</div>`;
}

// ── top-level wrapper ──
// Collapsed by default, matching every other Tier-3 <details class="card">
// in the Confirmation zone (Volatility, Advanced Analytics, Strategy
// Payoff / Institutional F&O Simulator) — one question, one card, opened
// after the Tier-1 verdict rather than competing with it for
// always-visible space (§3 of the IA redesign proposal).
ChainView.prototype.buildProbabilityHtml = function(d) {
  return `
  <details class="card" id="probability-card">
    <summary>
      <div class="card-head"><span class="ic">🧠</span>Probability<span class="fill"></span></div>
      <span class="chev">▶</span>
    </summary>
    <div class="detail-body">
      <div class="section-card sc-neutral" style="min-width:0;">
        <div class="section-header">
          <span class="section-title"><span class="section-icon">🧠</span>Smart Money Ranking <span class="section-sub">Visible-Range Ranking</span></span>
        </div>
        <div style="padding:2px 0;">${_probSmartMoneyRankingHtml(d)}</div>
        <div class="legend-foot" style="margin-top:6px;">Ranked by strongest ΔOI among institutional-flagged strikes (ACC / DIST / HEDGE / ROLL).</div>
      </div>
    </div>
  </details>`;
};
