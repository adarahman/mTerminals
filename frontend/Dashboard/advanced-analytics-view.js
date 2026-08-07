// ============================================================
// advanced-analytics-view.js
// "Advanced Analytics" — one collapsed-by-default <details class="card">
// on the main dashboard, tucking nine deep-dive views behind a single
// expand so the always-visible dashboard stays readable for new users:
//   0. Conviction Multiplier  4. OI Velocity       8. Capital Confirmation
//   1. Smart Money ranking    5. Per-strike Greeks 9. Futures-Options Divergence
//   2. IV Rank details        6. Scenario P&L
//   3. GEX table
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
// Must load after: formatters.js, chain-helpers.js (fmtI/fmtN/fmtK,
// getFilteredChain, activeAtm), dashboard-thresholds.js (INST_THRESHOLDS),
// engines/smart-money.js (smartMoneyBadge), conviction-gauge.js
// (buildConvictionGaugeHtml). Must load before dashboard.js.
// ============================================================

// ── shared sub-card shell ──
// icon/title/body match the .section-card / .section-header look every
// other dashboard card uses; linkFn/linkLabel (optional) render as the
// same .sec-btn "Full X →" pattern buildGreeksAlertsHtml already uses,
// so a sub-card can point at an existing full-page modal instead of
// re-implementing it. subtitle (optional, IA redesign step 2) renders as
// a .section-sub next to the title — same purpose as Institutional
// Activity Crux's .oic-sub: scope this card against others that answer
// a similarly-titled question differently (see dashboard-redesign-
// proposal.md §1's fragmentation table).
function _aaCardWrap(icon, title, bodyHtml, footnote, linkFn, linkLabel, subtitle) {
  const link = linkFn
    ? `<button class="sec-btn" style="padding:3px 9px;font-size:10.5px;" onclick="${linkFn}" title="${linkLabel}">${linkLabel}</button>`
    : '';
  return `<div class="section-card sc-neutral" style="min-width:0;">
    <div class="section-header">
      <span class="section-title"><span class="section-icon">${icon}</span>${title}${subtitle ? ` <span class="section-sub">${subtitle}</span>` : ''}</span>
      ${link}
    </div>
    <div style="padding:2px 0;">${bodyHtml}</div>
    ${footnote ? `<div class="legend-foot" style="margin-top:6px;">${footnote}</div>` : ''}
  </div>`;
}

// ── 1. Smart Money ranking ──
// Reuses the same isInst/badge heuristic SimulatorView's Institutional
// Activity table already applies per strike (see simulator-view.js's
// rowHtml), but ranks the WHOLE visible chain by strength of ΔOI among
// institutional-flagged strikes instead of grouping by near/far band —
// a "who's doing the most right now" list rather than a strike ledger.
function _aaSmartMoneyRankingHtml(d) {
  const chain = getFilteredChain(d);
  if (!chain.length) return _aaCardWrap('🧠', 'Smart Money Ranking', `<div class="dd-empty">No chain data yet.</div>`, null, null, null, 'Whole-Chain Ranking');

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

  const body = rows.length
    ? rows.map((row, i) => `
      <div style="display:flex;align-items:center;gap:10px;padding:7px 2px;${i < rows.length - 1 ? 'border-bottom:1px solid var(--border);' : ''}">
        <span>${row.badge.dot}</span>
        <span style="font-family:var(--mono);font-weight:600;flex:0 0 64px;">${fmtI(row.strike)}</span>
        <span style="flex:0 0 26px;color:var(--txt3);font-size:11px;">${row.dominant}</span>
        <span style="flex:1;font-weight:600;font-size:11px;color:${row.badge.color};">${row.badge.label}</span>
        <span style="font-family:var(--mono);color:${row.oiDomChg >= 0 ? 'var(--green)' : 'var(--amber)'};">${row.oiDomChg >= 0 ? '+' : '\u2212'}${fmtK(Math.abs(row.oiDomChg))}</span>
      </div>`).join('')
    : `<div class="dd-empty">No institutional-grade activity flagged right now.</div>`;

  return _aaCardWrap('🧠', 'Smart Money Ranking', body,
    'Ranked by strongest ΔOI among institutional-flagged strikes (ACC / DIST / HEDGE / ROLL).',
    null, null, 'Whole-Chain Ranking');
}

// ── 2. IV Rank details ──
// Distilled version of buildIvHvSkewDetailHtml's metric-strip (same
// fields, same "Full Surface →" destination) without its alert rows —
// this card's job is the raw numbers, the alerts already live inline
// in the always-visible Tier-3 card.
function _aaIvRankHtml(d) {
  const rank = d.ivRank || 0;
  const ivVsHv = (d.atmIV || 0) - (d.hv30 || 0);
  const richCheap = ivVsHv >= 0 ? 'rich' : 'cheap';
  const body = `<div class="metric-strip">
      <div class="metric-cell"><div class="k">IV Rank</div><div class="v">${Math.round(rank)}/100</div></div>
      <div class="metric-cell"><div class="k">ATM IV</div><div class="v">${fmtN(d.atmIV, 2)}%</div></div>
      <div class="metric-cell"><div class="k">HV (30d)</div><div class="v">${fmtN(d.hv30, 2)}%</div></div>
      <div class="metric-cell"><div class="k">IV vs HV</div><div class="v ${ivVsHv >= 0 ? 'bear' : 'bull'}">${fmtN(ivVsHv, 2)}% ${richCheap}</div></div>
      <div class="metric-cell"><div class="k">Skew</div><div class="v">${fmtN(d.atmSkew, 2)}%</div></div>
    </div>`;
  return _aaCardWrap('📉', 'IV Rank Details', body, null, 'openIvSurfaceModal()', 'Full Surface →');
}

// ── 3. GEX table ──
// Strikes ranked by |Net GEX| — the strongest dealer-hedging pressure
// points, rather than the full strike-ordered ledger (that stays behind
// "Full Table →", which opens the existing Greeks/GEX modal).
// IA redesign step 2: "Top |GEX| Strikes" scope tag distinguishes this
// per-strike ranked view from the Greeks Alerts card's whole-chain
// summed total and the Simulator's scenario-adjusted profile — see the
// scope note atop buildGreeksAlertsHtml (chain-greeks.js).
function _aaGexTableHtml(d) {
  const greeks = (d.greeks || []).slice().sort((a, b) => Math.abs(b.netGEX || 0) - Math.abs(a.netGEX || 0)).slice(0, 6);
  if (!greeks.length) return _aaCardWrap('\u03b3', 'GEX Table', `<div class="dd-empty">No GEX data yet.</div>`, null, null, null, 'Top |GEX| Strikes');
  const rows = greeks.map((g, i) => {
    const gex = g.netGEX || 0;
    const clr = gex >= 0 ? 'var(--blue)' : 'var(--red)';
    return `<div style="display:flex;align-items:center;gap:10px;padding:6px 2px;${i < greeks.length - 1 ? 'border-bottom:1px solid var(--border);' : ''}">
        <span style="font-family:var(--mono);font-weight:600;flex:0 0 64px;">${fmtI(g.strike)}</span>
        <span style="flex:1;font-family:var(--mono);color:${clr};">${fmtN(gex, 3)}B</span>
        <span style="font-size:11px;font-weight:600;color:${clr};">${gex >= 0 ? 'Long \u03b3' : 'Short \u03b3'}</span>
      </div>`;
  }).join('');
  return _aaCardWrap('\u03b3', 'GEX Table', rows, 'Strikes ranked by |Net GEX| magnitude.', 'openGreeksModal()', 'Full Table →', 'Top |GEX| Strikes');
}

// ── 4. OI Velocity ──
// Same oiVelocity[window].rows source the sidebar's Vel Window tabs and
// OI Flow modal already read (see chain-renderer.js's velBlock lookup),
// ranked by fastest ΔOI in the currently-selected window instead of
// strike order.
function _aaOiVelocityHtml(d) {
  const velBlock = (d.oiVelocity || []).find(b => b.window === _velWin) || (d.oiVelocity || [])[0];
  const rows = (velBlock && velBlock.rows) ? velBlock.rows : [];
  if (!rows.length) return _aaCardWrap('\u26a1', 'OI Velocity', `<div class="dd-empty">No velocity data yet.</div>`);

  const ranked = rows.map(r => ({
    strike: r.strike, ceDOI: r.ceDOI || 0, peDOI: r.peDOI || 0,
    mag: Math.max(Math.abs(r.ceDOI || 0), Math.abs(r.peDOI || 0))
  })).sort((a, b) => b.mag - a.mag).slice(0, 6);

  const body = ranked.map((r, i) => `
    <div style="display:flex;align-items:center;gap:10px;padding:6px 2px;${i < ranked.length - 1 ? 'border-bottom:1px solid var(--border);' : ''}">
      <span style="font-family:var(--mono);font-weight:600;flex:0 0 64px;">${fmtI(r.strike)}</span>
      <span style="flex:1;font-size:11px;color:var(--txt3);">CE <span style="font-family:var(--mono);color:${r.ceDOI >= 0 ? 'var(--green)' : 'var(--amber)'};">${r.ceDOI >= 0 ? '+' : '\u2212'}${fmtK(Math.abs(r.ceDOI))}</span></span>
      <span style="flex:1;font-size:11px;color:var(--txt3);">PE <span style="font-family:var(--mono);color:${r.peDOI >= 0 ? 'var(--green)' : 'var(--amber)'};">${r.peDOI >= 0 ? '+' : '\u2212'}${fmtK(Math.abs(r.peDOI))}</span></span>
    </div>`).join('');

  return _aaCardWrap('\u26a1', `OI Velocity (${_velWin}m)`, body, 'Strikes ranked by fastest ΔOI in the current window — change the window from the Range/Vel rail.');
}

// ── 5. Per-strike Greeks ──
// A narrow band around ATM (6 strikes) rather than the full chain — the
// complete strike-by-strike Δ/Γ/Θ/Vega + Net GEX table (with tab
// switching) stays behind "Full Table →", same modal as the GEX card
// above since they're the same underlying dataset in this app.
function _aaPerStrikeGreeksHtml(d) {
  const atm = activeAtm(d);
  const greeks = (d.greeks || []).slice()
    .sort((a, b) => Math.abs(a.strike - atm) - Math.abs(b.strike - atm))
    .slice(0, 6)
    .sort((a, b) => a.strike - b.strike);
  if (!greeks.length) return _aaCardWrap('\u0394', 'Per-Strike Greeks', `<div class="dd-empty">No Greeks data yet.</div>`);

  let rows = `<table class="t"><thead><tr><th>Strike</th><th>CE \u0394</th><th>PE \u0394</th><th>\u0393</th><th>\u0398/day</th><th>Vega</th></tr></thead><tbody>`;
  greeks.forEach(g => {
    const isAtm = g.strike === atm;
    rows += `<tr>
      <td class="${isAtm ? 'atm-sc' : 'sc'}">${fmtI(g.strike)}${isAtm ? ' \u2605' : ''}</td>
      <td>${fmtN(g.cDelta, 3)}</td>
      <td>${fmtN(g.pDelta, 3)}</td>
      <td>${fmtN(g.cGamma, 4)}</td>
      <td>${fmtN(g.cTheta, 2)}</td>
      <td>${fmtN(g.cVega, 2)}</td>
    </tr>`;
  });
  rows += `</tbody></table>`;
  return _aaCardWrap('\u0394', 'Per-Strike Greeks', rows, null, 'openGreeksModal()', 'Full Table →');
}

// ── 6. Scenario P&L ──
// Net-new: no existing card models P&L directly. Kept deliberately
// simple and transparent — a long ATM straddle's INTRINSIC value at
// expiry across a handful of spot-move scenarios, vs the premium paid
// today (d.callPremium + d.putPremium). This is expiry P&L, not a live
// mark-to-market re-price (that would need a full Black-Scholes repricing
// per scenario, which the Institutional F&O Simulator's GEX view already
// approximates for gamma exposure — this card answers a different
// question: "what does this position return if held to expiry").
function _aaScenarioPnlHtml(d) {
  const spot = d.spot || 0;
  const atm = activeAtm(d);
  const straddle = (d.callPremium || 0) + (d.putPremium || 0);
  if (!spot || !straddle) return _aaCardWrap('\ud83c\udfaf', 'Scenario P&L', `<div class="dd-empty">No premium data yet.</div>`);

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

  return _aaCardWrap('\ud83c\udfaf', 'Scenario P&L',
    `<table class="t"><thead><tr><th>Spot Move</th><th>Scenario Spot</th><th>Straddle P&amp;L</th></tr></thead><tbody>${rows}</tbody></table>`,
    'Long ATM straddle payoff if held to expiry, per spot-move scenario — not a live mark-to-market re-price.');
}

// ── 8. Capital Confirmation ──
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

// ── 9. Futures-Options Divergence ──
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
      <div style="font-size:11px;color:var(--txt3);margin-bottom:10px;">Conviction \u00b7 Smart Money \u00b7 IV Rank \u00b7 GEX \u00b7 OI Velocity \u00b7 Greeks \u00b7 Scenario P&amp;L \u00b7 Capital Confirmation \u00b7 Futures\u2013Options Divergence</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div style="grid-column:1/-1;">${buildConvictionGaugeHtml(d)}</div>
        ${_aaSmartMoneyRankingHtml(d)}
        ${_aaIvRankHtml(d)}
        ${_aaGexTableHtml(d)}
        ${_aaOiVelocityHtml(d)}
        ${_aaPerStrikeGreeksHtml(d)}
        ${_aaScenarioPnlHtml(d)}
        ${_aaCapitalConfirmationHtml(d)}
        ${_aaFuturesOptionsDivergenceHtml(d)}
      </div>
    </div>
  </details>`;
};