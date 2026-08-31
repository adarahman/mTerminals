// ============================================================
// advanced-analytics-view.js
// "Advanced Analytics" — one collapsed-by-default <details class="card">
// on the main dashboard, tucking six remaining deep-dive views behind
// a single expand so the always-visible dashboard stays readable for new
// users:
//   0. Conviction Multiplier  3. Per-strike Greeks
//   1. GEX table              4. Capital Confirmation
//   2. OI Velocity            5. Futures-Options Divergence
// Each sub-card is a compact, self-contained read built off data already
// on the payload / already computed elsewhere (chain, greeks, oiVelocity,
// volOiRatios) — no new backend fields required. Where a fuller view
// already exists as its own modal (Greeks & GEX, IV Surface), the
// sub-card links straight to it instead of duplicating that table's
// live-tick wiring. Conviction Multiplier (0) is the odd one out — it's
// not a table but a verdict + reasons card, spanning both grid columns —
// moved here from its old always-visible slot under the Decision Engine
// since it's a derived confirm/conflict check built entirely from data
// shown elsewhere (FII/DII, gamma flip, PCR expansion, smart money lean),
// not an at-a-glance fact of its own.
//
// IA redesign step 7 (dashboard-redesign-proposal.md §2.3/§5): decomposing
// this card into purpose-specific ones is a multi-pass effort, not a
// single refactor. Three passes done: IV Rank details moved out to its
// own "Volatility" card (volatility-view.js), Smart Money Ranking moved
// out to its own "Probability" card (probability-view.js), and Scenario
// P&L moved out to its own "Scenario Analysis" card
// (scenario-analysis-view.js) — three of the four destinations §2.3
// names (Volatility / Scenario Analysis / Probability / Cross-Market).
// A future Cross-Market destination has no candidate content yet in this
// codebase, so nothing is pending extraction for it. GEX table /
// OI Velocity / Per-strike Greeks / Capital Confirmation /
// Futures-Options Divergence don't map cleanly to any of the four named
// destinations and stay here for now — this is likely close to Advanced
// Analytics' final shape unless a genuinely new question emerges.
//
// Must load after: formatters.js, chain-helpers.js (fmtI/fmtN/fmtK,
// getFilteredChain, activeAtm), dashboard-thresholds.js (INST_THRESHOLDS),
// engines/smart-money.js (smartMoneyBadge), conviction-gauge.js
// (buildConvictionGaugeHtml). Must load before dashboard.js.
// ============================================================

// ── shared sub-card shell ──
// icon/title/body match the .section-card / .section-header look every
// other dashboard card uses. When linkFn/linkLabel are given, the whole
// header line becomes the click target — same nav-card-header pattern as
// Option Chain Snapshot / Greeks / FII/DII / IV Rank (components.css's
// button.section-header.nav-card-header), rather than a separate "Full
// X →" button sitting next to a static header — so a sub-card can point
// at an existing full-page modal without adding a second click target.
// subtitle (optional, IA redesign step 2) renders as a .section-sub next
// to the title — same purpose as Institutional Activity Crux's .oic-sub:
// scope this card against others that answer a similarly-titled question
// differently (see dashboard-redesign-proposal.md §1's fragmentation
// table).
function _aaCardWrap(icon, title, bodyHtml, footnote, linkFn, linkLabel, subtitle) {
  const titleHtml = `<span class="section-icon">${icon}</span>${title}${subtitle ? ` <span class="section-sub">${subtitle}</span>` : ''}`;
  const header = linkFn
    ? `<button class="section-header nav-card-header" onclick="${linkFn}"
         aria-label="Open ${title} — view full table" title="Open full ${title.toLowerCase()}">
        <span class="section-title nav-card-header-label">${titleHtml}</span>
        <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
      </button>`
    : `<div class="section-header">
        <span class="section-title">${titleHtml}</span>
      </div>`;
  return `<div class="section-card sc-neutral" style="min-width:0;">
    ${header}
    <div style="padding:2px 0;">${bodyHtml}</div>
    ${footnote ? `<div class="legend-foot" style="margin-top:6px;">${footnote}</div>` : ''}
  </div>`;
}

// ── Capital Confirmation ──
// analytics/capital_futures_confirmation.py's compute_capital_confirmation()
// output (spec item #3) — three-vote agreement check (capital flow /
// market regime / price) with an elevated-volume upgrade from Weak to
// Confirmed. See that module's docstring for the vote definitions.
function _aaCapitalConfirmationHtml(d) {
  const c = d.capitalConfirmation || {};
  if (!c.confirmation) return _aaCardWrap('\u2696\ufe0f', 'Capital Confirmation', `<div class="dd-empty">No capital/regime data yet.</div>`);

  const confColor = c.confirmation === 'Confirmed Bullish' ? 'var(--green)'
    : c.confirmation === 'Confirmed Bearish' ? 'var(--red)'
    : c.confirmation === 'Divergence' ? 'var(--amber)'
    : 'var(--txt3)';

  const voteChip = (label, vote) => {
    const clr = vote > 0 ? 'var(--green)' : vote < 0 ? 'var(--red)' : 'var(--txt3)';
    const txt = vote > 0 ? 'Bullish' : vote < 0 ? 'Bearish' : 'Neutral';
    return `<div style="padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg2);">
      <div style="font-size:9.5px;color:var(--txt3);">${label}</div>
      <div style="font-size:11px;font-weight:700;color:${clr};">${txt}</div>
    </div>`;
  };

  const body = `
    <div style="font-size:14px;font-weight:700;color:${confColor};margin-bottom:8px;">${c.confirmation}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      ${voteChip('Capital Flow', c.capitalVote)}
      ${voteChip('Regime', c.regimeVote)}
      ${voteChip('Price', c.priceVote)}
    </div>
    ${c.volumeRatio != null ? `<div style="margin-top:8px;font-size:10.5px;color:var(--txt3);">Vol/OI ${fmtN(c.volumeRatio * 100, 1)}% — ${c.volumeElevated ? 'elevated turnover' : 'normal turnover'}</div>` : ''}`;

  return _aaCardWrap('\u2696\ufe0f', 'Capital Confirmation', body,
    'Agreement across capital flow, market regime, and price direction — elevated volume upgrades a 2/3 agreement to Confirmed.');
}

// ── Futures-Options Divergence ──
// analytics/capital_futures_confirmation.py's detect_futures_options_
// divergence() output (spec item #4).
function _aaFuturesOptionsDivergenceHtml(d) {
  const fo = d.futuresOptionsDivergence || {};
  if (!fo.status) return _aaCardWrap('\ud83d\udd00', 'Futures\u2013Options Divergence', `<div class="dd-empty">No regime/capital data yet.</div>`);

  const statusColor = fo.status === 'Aligned' ? 'var(--green)'
    : fo.status === 'Insufficient Data' ? 'var(--txt3)'
    : 'var(--amber)';

  const sideChip = (label, side) => {
    const clr = side === 'Bullish' ? 'var(--green)' : side === 'Bearish' ? 'var(--red)' : 'var(--txt3)';
    return `<div style="padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg2);">
      <div style="font-size:9.5px;color:var(--txt3);">${label}</div>
      <div style="font-size:11px;font-weight:700;color:${clr};">${side}</div>
    </div>`;
  };

  const body = `
    <div style="font-size:13px;font-weight:700;color:${statusColor};margin-bottom:8px;">${fo.status}</div>
    <div style="display:flex;gap:8px;margin-bottom:8px;">
      ${sideChip('Futures', fo.futuresSide)}
      ${sideChip('Options', fo.optionsSide)}
    </div>
    <div class="story" style="color:var(--txt3);">${fo.description || ''}</div>`;

  return _aaCardWrap('\ud83d\udd00', 'Futures\u2013Options Divergence', body,
    'Flags when futures positioning and options capital flow point opposite ways \u2014 a possible trap or hedging, not fresh conviction.');
}

// ── top-level wrapper ──
// Collapsed by default (no `open` attribute, matching every other Tier-3
// <details class="card">), placed right after the Tier-3 row so it reads
// as "more detail, further down" rather than competing with the
// always-visible Decision/Executive/Chain Snapshot cards above it.
ChainView.prototype.buildAdvancedAnalyticsHtml = function(d) {
  return `
  <details class="card" id="advanced-analytics-card">
    <summary>
      <div class="card-head"><span class="ic">\ud83d\udd2c</span>Advanced Analytics<span class="fill"></span></div>
      <span class="chev">\u25b6</span>
    </summary>
    <div class="detail-body">
      <div style="font-size:11px;color:var(--txt3);margin-bottom:10px;">Conviction \u00b7 Capital Confirmation \u00b7 Futures\u2013Options Divergence</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div style="grid-column:1/-1;">${buildConvictionGaugeHtml(d)}</div>
        ${_aaCapitalConfirmationHtml(d)}
        ${_aaFuturesOptionsDivergenceHtml(d)}
      </div>
    </div>
  </details>`;
};
