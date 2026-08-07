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
function _scenPnlHtml(d) {
  const spot = d.spot || 0;
  const atm = activeAtm(d);
  const straddle = (d.callPremium || 0) + (d.putPremium || 0);
  if (!spot || !straddle) return `<div class="dd-empty">No premium data yet.</div>`;

  const moves = [-0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03];
  const rows = moves.map(m => {
    const scenSpot = spot * (1 + m);
    const intrinsic = Math.max(scenSpot - atm, 0) + Math.max(atm - scenSpot, 0);
    const pnl = intrinsic - straddle;
    const isBase = m === 0;
    return `<tr>
      <td class="${isBase ? 'atm-sc' : 'sc'}">${m > 0 ? '+' : ''}${(m * 100).toFixed(0)}%</td>
      <td style="font-family:var(--mono);">${fmtI(scenSpot)}</td>
      <td style="font-family:var(--mono);color:${pnl >= 0 ? 'var(--green)' : 'var(--red)'};">${pnl >= 0 ? '+' : '\u2212'}\u20b9${fmtN(Math.abs(pnl), 2)}</td>
    </tr>`;
  }).join('');

  return `<table class="t"><thead><tr><th>Spot Move</th><th>Scenario Spot</th><th>Straddle P&amp;L</th></tr></thead><tbody>${rows}</tbody></table>`;
}

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
        <div class="legend-foot" style="margin-top:6px;">Long ATM straddle payoff if held to expiry, per spot-move scenario — not a live mark-to-market re-price.</div>
      </div>
    </div>
  </details>`;
};
