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
  const hasRank = d.ivRank != null;
  const hasHv30 = d.hv30 != null;
  const hasIvVsHv = d.atmIV != null && hasHv30;
  const ivVsHv = hasIvVsHv ? (d.atmIV - d.hv30) : null;
  const richCheap = ivVsHv != null && ivVsHv >= 0 ? 'rich' : 'cheap';
  return `<div class="metric-strip">
      <div class="metric-cell"><div class="k">IV Rank</div><div class="v">${hasRank ? Math.round(d.ivRank) : '—'}/100</div></div>
      <div class="metric-cell"><div class="k">ATM IV</div><div class="v">${fmtN(d.atmIV, 2)}%</div></div>
      <div class="metric-cell"><div class="k">HV (30d)</div><div class="v">${hasHv30 ? fmtN(d.hv30, 2) + '%' : '—'}</div></div>
      <div class="metric-cell"><div class="k">IV vs HV</div><div class="v ${ivVsHv != null ? (ivVsHv >= 0 ? 'bear' : 'bull') : ''}">${ivVsHv != null ? fmtN(ivVsHv, 2) + '% ' + richCheap : '—'}</div></div>
      <div class="metric-cell"><div class="k">Skew</div><div class="v">${fmtN(d.atmSkew, 2)}%</div></div>
    </div>`;
}

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
