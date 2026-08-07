// ============================================================
// exec-view.js
// Split out of panels-views.js. ExecView — Executive Dashboard /
// Decision panel. Depends on dashboard-thresholds.js (INST_THRESHOLDS);
// load after it.
// ============================================================

// Hoisted — was defined identically inside both renderExecutiveDashboard
// and buildFiiDiiComparisonHtml.
const fmtSigned = (v, dp=0) => {
  const n = Number(v)||0;
  return `${n>=0?'+':''}${fmtN(n,dp)}`;
};

class ExecView {
  renderExecutiveDashboard(d){
  // ── Use Decision Engine output if available ───────────────────────────────
  const dec       = d.decision || {};
  const decBias   = dec.bias || '';           // BULLISH | BEARISH | NEUTRAL | CONFLICTED

  // isBearBias/isBullBias now shared via chain-helpers.js — same semantics
  // as before in this file (decision.bias governs when present).
  const isBull = isBullBias(d);
  const isBear = isBearBias(d);

  const pcr    = d.totalPCR || 1;


  return `
<div id="exec-section-wrap">
<div class="exec-grid">

  <!-- ── CARD 1: MARKET HEALTH + STORY (merged) ── -->
  <!-- One narrative card: raw canonical context + a concise market story.
       Presentation-layer Momentum/OI/Theta scores were removed in P1 so
       this card explains the state instead of acting like a second
       decision engine. Max Pain remains owned by D-04 Chain Snapshot. -->
  <div class="exec-card c-blue">
    <div class="exec-title">📊 Market Health &amp; Story</div>
    ${(() => {
      const oiChgPcr = d.oiChgPCR || pcr;
      const atmStraddlePrem = (d.callPremium||0) + (d.putPremium||0);
      const stratName = dec.autoStrategy?.name || null;
      const storyText = isBull
        ? 'Put-side support and price structure remain constructive; prefer buying weakness while the thesis holds.'
        : isBear
          ? 'Call-side pressure and price structure remain defensive; prefer selling strength while the thesis holds.'
          : 'Positioning is mixed; wait for clearer price/flow agreement before increasing conviction.';
      return `
        <div style="font-size:12px;line-height:1.55;color:var(--txt2);padding:2px 0 10px;">${storyText}</div>
        <div class="kv-grid">
          <div class="kv"><span class="k">Spot Move</span><span class="v" style="color:${signColor(d.spotChgPct,'var(--txt3)')};">${fmtSigned(d.spotChgPct||0,2)}%</span></div>
          <div class="kv"><span class="k">Fut Basis</span><span class="v">${fmtSigned(d.basis||0,1)}</span></div>
          <div class="kv"><span class="k">Total PCR</span><span class="v">${fmtN(pcr,2)}</span></div>
          <div class="kv"><span class="k">ΔOI PCR</span><span class="v">${fmtN(oiChgPcr,2)}</span></div>
          <div class="kv"><span class="k">ATM Θ / day</span><span class="v">${fmtN(d.atmTheta,2)}</span></div>
          <div class="kv"><span class="k">DTE</span><span class="v">${d.dte||0}</span></div>
        </div>
        <div style="margin-top:9px;padding-top:8px;border-top:1px solid var(--border);">
          <div class="story">±Expected Move <strong style="color:var(--blue);">${Math.round(atmStraddlePrem)}</strong></div>
          <div class="story">ATM Straddle Prem <strong>CE ₹${fmtN(d.callPremium||0,1)} + PE ₹${fmtN(d.putPremium||0,1)}</strong></div>
          ${stratName ? `<div class="story">Engine Pick <strong style="color:var(--amber);">${stratName}</strong></div>` : ''}
        </div>`;
    })()}
  </div>

  <!-- ── CARD 2: GREEKS / NET GEX ── -->
  <!-- Moved here from row2 (chain-renderer.js's Tier-2 row) so it sits
       immediately left of the Option Chain Snapshot card in the same row,
       per the updated layout. activeAtm()/getFilteredChain() are the same
       global helpers chain-renderer.js uses ahead of this same call. -->
  ${(() => {
    const gAtm = activeAtm(d);
    // getVisibleRangeGreeks (metrics.js, IA redesign step 6) — same
    // visible-range filter chain-renderer.js's renderDashboard/
    // _rerenderChainPanels use for this same card's per-tick patch, so
    // the initial render and every subsequent tick agree on scope.
    const gGreeks = getVisibleRangeGreeks(d);
    return app.chain.buildGreeksAlertsHtml(gGreeks, gAtm, d);
  })()}

  <!-- ── CARD 3: OPTION CHAIN SNAPSHOT ── -->
  <!-- Top Movers (Drivers/Draggers) moved out of this slot — pending its
       own standalone surface if it is reintroduced in a future PDS revision.
       app.chain is the live ChainView instance (see dashboard.js), so this
       reuses the exact same builder/markup as the row2 Snapshot card. -->
  ${app.chain.buildChainSummaryHtml(d)}

  <!-- Cards 4-6 (Market Regime & Smart Money / Institutional Footprint
       Score / Capital Concentration) moved out of this grid — IA redesign
       step 1 (Zone reorg, see dashboard-redesign-proposal.md §2.1/§5).
       Those three are Institutional-zone cards, not Structure &
       Positioning; they now render from renderInstitutionalGrid() below,
       called separately by chain-renderer.js's renderDashboard() so they
       sit with Institutional Activity Crux / Smart Money Ranking instead
       of wrapping onto this grid's second row. No computation changes —
       same three builder calls, just moved to a different container. -->

</div>

<!-- FII/DII Sentiment used to render here, full-width below the exec
     grid. It now lives in the Capital Flow zone (chain-renderer.js),
     grouped with OI Flow — see renderDashboard() and layout.css's
     .row2. -->
<!-- Institutional Activity Crux used to render here too, full-width
     below FII/DII. It now renders from renderInstitutionalGrid() below,
     alongside the other Institutional-zone cards, instead of pairing
     with Greeks by Moneyness in the old #sec-tier2 row. -->
</div>
`;}

  // ── INSTITUTIONAL ZONE GRID (Zone E) ──
  // IA redesign step 1: Market Regime & Smart Money / Institutional
  // Footprint Score / Capital Concentration used to render as cards 4-6
  // of the Structure exec-grid above, purely because that's where the
  // Institutional Positioning Analytics layer was first added — not
  // because they answer a "structure" question. They're institutional-
  // intent cards, so they now render together with Institutional
  // Activity Crux in their own zone (chain-renderer.js calls this right
  // before the Institutional Activity Crux card). Same three builder
  // calls as before, same .exec-grid CSS class (3-col, wraps to its own
  // row for a 3-card set) — layout-only move, no computation changes.
  renderInstitutionalGrid(d){
    return `
<div class="exec-grid">
  ${this.buildMarketRegimeCard(d)}
  ${this.buildFootprintScoreCard(d)}
  ${this.buildCapitalConcentrationCard(d)}
</div>
`;
  }

  // ── INSTITUTIONAL ACTIVITY CRUX (main dashboard card) ──
  // Same "always-visible read, full detail lives elsewhere" pattern as
  // buildFiiDiiSummaryCard() above: the Strike Detail table (Simulator
  // panel) only ever windows to the 10 strikes nearest ATM, so a flagged
  // strike outside that window was previously invisible anywhere on the
  // main dashboard. This card scans the FULL visible chain — near and far
  // band alike, using the exact same instBandFor()/INST_THRESHOLDS logic
  // the table and Vol/OI bars use — and rolls it up into one glanceable
  // summary: how many strikes are flagged in each band, which side (CE/PE)
  // the flagged strikes lean toward, and the single strongest signal.
  buildInstitutionalActivitySummaryCard(d){
  const chain = d.chain || [];
  const ranked = d.footprintRanked || [];
  const ratios = d.volOiRatios || {};
  const atm = d.atm || (d.ctx && d.ctx.atm) || 0;
  const greeksData = d.greeks || [];
  const step = greeksData.length > 1 ? (greeksData[1].strike - greeksData[0].strike) : 50;

  if(!chain.length || !atm){
    return `
  <div class="oic-card" id="inst-activity-summary-card">
    <button class="oic-head nav-card-header" onclick="openStrikeDetailReportModal()"
       aria-label="Open Institutional Activity Crux — view Strike Detail report" title="Open Strike Detail report">
      <div class="oic-head-left">
        <span class="oic-icon icon-amber">🏛️</span>
        <span class="oic-title nav-card-header-label">Institutional Activity Crux</span>
      </div>
      <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
    </button>
    <div class="oic-empty">Awaiting chain data…</div>
  </div>`;
  }

  // P2 ownership cleanup: significance comes from the canonical backend
  // footprintRanked primitive. This card no longer re-applies independent
  // median-OI / Vol-OI thresholds in the presentation layer. We only enrich
  // canonical ranked strikes with current chain fields for the compact ledger.
  const chainByStrike = new Map(chain.map(r => [Number(r.strike), r]));
  const flagged = ranked.map(r => {
    const strike = Number(r.strike);
    const row = chainByStrike.get(strike) || {};
    const rawRatio = ratios[String(strike)] || {};
    const totalOI = (row.ceOI||0) + (row.peOI||0);
    const volRatio = totalOI > 0 ? ((rawRatio.ce||0) + (rawRatio.pe||0)) / 2 : 0;
    const side = r.dominantSide || ((row.ceOI||0) >= (row.peOI||0) ? 'CE' : 'PE');
    return {
      strike,
      band: instBandFor(strike, atm, step),
      oiDominant: side,
      totalOI,
      dominantDOI: side === 'CE' ? (row.ceChgOI||0) : (row.peChgOI||0),
      volRatio,
      strength: Number(r.footprintScore)||0,
    };
  }).filter(r => Number.isFinite(r.strike));

  const nearCount = flagged.filter(f => f.band==='near').length;
  const farCount  = flagged.filter(f => f.band==='far').length;
  const ceCount   = flagged.filter(f => f.oiDominant==='CE').length;
  const peCount   = flagged.filter(f => f.oiDominant==='PE').length;

  let biasLabel = 'Balanced';
  if(ceCount > peCount) biasLabel = 'CE-heavy (bearish tilt)';
  else if(peCount > ceCount) biasLabel = 'PE-heavy (bullish tilt)';

  const top = flagged.slice().sort((a,b) => b.strength - a.strength)[0];
  const nearLedger = flagged.filter(f => f.band === 'near').sort((a,b) => b.strength - a.strength).slice(0,5);
  const biasBadgeClass = ceCount > peCount ? 'b-red' : peCount > ceCount ? 'b-green' : 'b-blue';
  const signalClr = top && top.oiDominant==='CE' ? 'var(--neg)' : 'var(--pos)';

  return `
  <div class="oic-card" id="inst-activity-summary-card">
    <button class="oic-head nav-card-header" onclick="openStrikeDetailReportModal()"
       aria-label="Open Institutional Activity Crux — view Strike Detail report" title="Open Strike Detail report">
      <div class="oic-head-left">
        <span class="oic-icon icon-amber">🏛️</span>
        <span class="oic-title nav-card-header-label">Institutional Activity Crux <span class="oic-sub">Canonical Footprint • Near-ATM Ledger</span></span>
      </div>
      <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
    </button>
    ${flagged.length===0 ? `
    <div class="oic-empty">No canonical footprint strikes available.</div>
    ` : `
    <div class="oic-badges">
      <span class="oic-badge b-blue"><span class="lbl">NEAR</span><span class="val">${nearCount} ranked</span></span>
      <span class="oic-badge b-amber"><span class="lbl">FAR</span><span class="val">${farCount} ranked</span></span>
      <span class="oic-badge ${biasBadgeClass}"><span class="lbl">BIAS</span><span class="val">${biasLabel}</span></span>
    </div>
    ${top ? `<div class="oic-signal"><span class="lbl">Strongest</span><button class="strike-link ${top.oiDominant==='CE'?'ce':'pe'}" onclick="event.stopPropagation();openOptionChainAtStrike(${top.strike})">${fmtI(top.strike)} ${top.oiDominant}</button><span style="color:${signalClr};font-family:var(--mono);font-weight:700;">${fmtN(top.strength,0)}</span></div>` : ''}
    ${nearLedger.length ? `
    <div class="oic-ledger-wrap">
      <div class="oic-ledger-head"><span>Strike</span><span>Side</span><span>OI</span><span>ΔOI</span><span>Vol/OI</span></div>
      ${nearLedger.map(r => `<button type="button" class="oic-ledger-row" onclick="event.stopPropagation();openOptionChainAtStrike(${r.strike})" title="Open Option Chain at ${fmtI(r.strike)}"><span>${fmtI(r.strike)}</span><span class="${r.oiDominant==='CE'?'ce':'pe'}">${r.oiDominant}</span><span>${fmtK(r.totalOI)}</span><span style="color:${signColor(r.dominantDOI,'var(--txt3)')};">${fmtSigned(r.dominantDOI,0)}</span><span>${fmtN(r.volRatio,2)}</span></button>`).join('')}
    </div>` : `<div class="oic-empty" style="margin-top:8px;">No ranked footprint strike is currently inside the near-ATM band.</div>`}
    `}
  </div>`;
}

  // ── FII / DII CRUX SUMMARY (main dashboard card) ──
  // A compact, always-visible read of each participant's Index Fut Net OI
  // (same s.${p}_index_fut_net fields buildFiiDiiCard's full modal table
  // uses) plus any divergence flags, and a "Full Table →" button that
  // opens the full comparison table in its own modal instead of
  // rendering inline. Previously showed each participant's composite
  // sentiment WORD (Bullish/Bearish/Mixed) instead of a number — that
  // read fine as a .kv-grid row shape, but doesn't match the mockup's
  // intent for this card, which is a numeric position readout (like
  // "-2,140 Cr") colored by sign, not a label.
  // ── FII / DII BIAS SUMMARY BLOCK ──
  // d.fiiDiiBias is analytics/fii_dii_market_bias.py's get_market_bias_report()
  // output verbatim (backend/mTerminals_json.py caches + exposes it on the
  // main tick as "fiiDiiBias" so this read doesn't depend on the modal's
  // live /dashboard-relay connection). This is now the ONLY place this bias
  // card renders — the modal used to show the same read a second time via
  // fdRenderBias()/#fdBiasCard, purely repeating what's already visible
  // here above the "Full Table →" button; that copy was removed from
  // DashboardPro.html and fiidii-report.js. Still uses the .fd-bias-*
  // classes (fiidii-report.css, loaded globally, not modal-scoped) rather
  // than duplicating that CSS here.
  // ── MARKET REGIME & SMART MONEY (main dashboard card) ──
  // d.marketRegime is analytics/market_regime.py's classify_market_regime()
  // output, d.smartMoneySummary is analytics/smart_money_summary.py's
  // compute_smart_money_summary() output — both computed fresh every tick
  // in engine.py/mTerminals_json.py (NOT day-cached like fiiDiiBias above).
  // regime === "Indeterminate" whenever there's no futures OI baseline yet
  // (first tick of the session/contract) or the price/OI move is inside
  // the flat-deadband — shown as a neutral "reading market…" state rather
  // than a misleadingly confident badge.
  buildMarketRegimeCard(d){
  const mr = d.marketRegime || {};
  const sm = d.smartMoneySummary || {};
  const regime = mr.regime || 'Indeterminate';
  const hasRegime = regime !== 'Indeterminate';

  const regimeColor = {
    'Long Build-up':  'var(--green)',
    'Short Covering': 'var(--green)',
    'Short Build-up': 'var(--red)',
    'Long Unwinding': 'var(--red)',
  }[regime] || 'var(--amber)';

  const biasColor = sm.bias === 'Bullish' ? 'var(--green)'
                   : sm.bias === 'Bearish' ? 'var(--red)'
                   : 'var(--amber)';

  const confirmTxt = sm.capitalConfirms === true  ? '✓ capital confirms'
                    : sm.capitalConfirms === false ? '✗ capital diverges'
                    : 'capital data pending';
  const confirmColor = sm.capitalConfirms === true ? 'var(--green)'
                      : sm.capitalConfirms === false ? 'var(--red)'
                      : 'var(--txt3)';

  return `
  <div class="exec-card c-blue">
    <div class="exec-title">🧭 Market Regime &amp; Smart Money</div>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <span style="font-size:13px;font-weight:700;color:${regimeColor};">${regime}</span>
      ${hasRegime ? `<span style="font-size:10px;color:var(--txt3);">(${mr.confidence||0}% confidence)</span>` : ''}
    </div>
    ${hasRegime ? this.progress('Regime Confidence', mr.confidence||0, regimeColor) : `
    <div class="story" style="color:var(--txt3);">${mr.description || 'Reading market — waiting on futures OI baseline.'}</div>`}
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;padding-top:8px;border-top:1px solid var(--border);">
      <div>
        <div style="font-size:10px;color:var(--txt3);">Price Chg</div>
        <div style="font-size:13px;font-weight:700;color:${signColor(mr.price_chg_pct,'var(--txt3)')};">${fmtSigned(mr.price_chg_pct,2)}%</div>
      </div>
      <div>
        <div style="font-size:10px;color:var(--txt3);">Futures OI Chg</div>
        <div style="font-size:13px;font-weight:700;color:${signColor(mr.fut_oi_chg_pct,'var(--txt3)')};">${fmtSigned(mr.fut_oi_chg_pct,2)}%</div>
      </div>
    </div>
    <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border);">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:12px;font-weight:700;color:${biasColor};">${sm.bias || 'Neutral'}</span>
        <span style="font-size:10px;color:${confirmColor};">${confirmTxt}</span>
      </div>
      <div class="story" style="margin-top:4px;">${sm.summary || ''}</div>
    </div>
  </div>`;
}

  // ── INSTITUTIONAL FOOTPRINT SCORE (main dashboard card) ──
  // d.footprintRanked: oi.footprint_score.rank_footprint_strikes() top-8,
  // already sorted descending. footprintScore is a percentile rank
  // against the rest of TODAY'S visible chain (0-100), not an absolute
  // scale — see that module's docstring for why. dominantSide flags
  // which leg (CE/PE) is driving that strike's score.
  buildFootprintScoreCard(d){
  const ranked = d.footprintRanked || [];
  if(!ranked.length){
    return `
  <div class="exec-card c-amber">
    <div class="exec-title">👣 Institutional Footprint Score</div>
    <div class="dd-empty">No footprint data yet.</div>
  </div>`;
  }
  const rows = ranked.slice(0,6).map((r,i)=>{
    const clr = r.footprintScore>=70 ? 'var(--red)' : r.footprintScore>=40 ? 'var(--amber)' : 'var(--txt3)';
    return `
    <div style="display:flex;align-items:center;gap:8px;padding:5px 0;${i<Math.min(ranked.length,6)-1?'border-bottom:1px solid var(--border);':''}">
      <span style="font-family:var(--mono);font-weight:600;flex:0 0 64px;">${fmtI(r.strike)}</span>
      <span style="flex:0 0 28px;font-size:10px;color:var(--txt3);">${r.dominantSide}</span>
      <div class="p-bar" style="flex:1;"><div class="p-fill" style="width:${r.footprintScore}%;background:${clr};"></div></div>
      <strong style="font-size:11px;font-family:var(--mono);color:${clr};flex:0 0 34px;text-align:right;">${fmtN(r.footprintScore,0)}</strong>
    </div>`;
  }).join('');
  return `
  <div class="exec-card c-amber">
    <div class="exec-title">👣 Institutional Footprint Score</div>
    ${rows}
  </div>`;
}

  // ── CAPITAL CONCENTRATION (main dashboard card) ──
  // d.capitalConcentration: oi.footprint_score.compute_capital_
  // concentration()'s output — top-5 strikes by total premium locked
  // (CE+PE combined) and what % of the visible chain's whole capital
  // they hold.
  buildCapitalConcentrationCard(d){
  const cc = d.capitalConcentration || {};
  const top = cc.topStrikes || [];
  const ceWall = d.capitalCeWallStrike;
  const peWall = d.capitalPeWallStrike;
  if(!top.length && !ceWall && !peWall){
    return `
  <div class="exec-card c-green">
    <div class="exec-title">🎯 Capital Concentration</div>
    <div class="dd-empty">No capital data yet.</div>
  </div>`;
  }
  const pct = cc.concentrationPct || 0;
  const pctColor = pct>=70 ? 'var(--red)' : pct>=45 ? 'var(--amber)' : 'var(--green)';
  const rows = top.map((s,i)=>`
    <div style="display:flex;align-items:center;justify-content:space-between;padding:5px 0;${i<top.length-1?'border-bottom:1px solid var(--border);':''}">
      <button class="strike-link" onclick="event.stopPropagation();openOptionChainAtStrike(${s.strike})">${fmtI(s.strike)}</button>
      <span style="font-family:var(--mono);color:var(--txt);">₹${fmtK(s.capitalLocked)}</span>
    </div>`).join('');
  return `
  <div class="exec-card c-green">
    <div class="exec-title">🎯 Capital Concentration</div>
    <div class="capital-wall-owner" aria-label="Canonical capital walls">
      ${ceWall ? `<div><span>₹ CE Wall</span><button class="strike-link ce" onclick="event.stopPropagation();openOptionChainAtStrike(${ceWall})">${fmtI(ceWall)}</button></div>` : ''}
      ${peWall ? `<div><span>₹ PE Wall</span><button class="strike-link pe" onclick="event.stopPropagation();openOptionChainAtStrike(${peWall})">${fmtI(peWall)}</button></div>` : ''}
    </div>
    <div style="display:flex;align-items:baseline;gap:6px;margin-bottom:8px;">
      <span style="font-size:20px;font-weight:700;color:${pctColor};">${fmtN(pct,1)}%</span>
      <span style="font-size:10px;color:var(--txt3);">of chain capital in top ${top.length} strikes</span>
    </div>
    ${rows}
  </div>`;
}

  buildFiiDiiBiasHtml(bias){
  if(!bias || !bias.asOf) return '';
  const l = (bias.overallLabel || '').toLowerCase();
  const cls = l === 'bullish' ? 'fd-up' : l === 'bearish' ? 'fd-down' : 'fd-flat';
  const narrative = (bias.narrative || []).map(n => `<li>${n}</li>`).join('');
  const caveats = (bias.caveats || []).map(c => `<li>${c}</li>`).join('');
  return `
    <div class="fd-bias-card" style="margin-bottom:10px;">
      <div class="fd-bias-head">
        <span class="fd-bias-label ${cls}">${bias.overallLabel || 'Unscored'}</span>
        <span class="fd-bias-score ${cls}">score ${bias.overallScore>=0?'+':''}${bias.overallScore}</span>
        <span class="fd-bias-confidence">confidence ${bias.overallConfidence}%</span>
      </div>
      <ul class="fd-bias-narrative">${narrative}</ul>
      <ul class="fd-bias-caveats">${caveats}</ul>
    </div>`;
}

  buildFiiDiiSummaryCard(d){
  const bias = d.fiiDiiBias || {};
  const cash = bias.cash || {};
  const hasCash = !!cash.available;
  const dateLabel = cash.latestDate || bias.asOf || '—';

  const cashVal = (v) => {
    const n = Number(v);
    if(!Number.isFinite(n)) return '—';
    return `${n>=0?'+':''}₹${fmtN(n,0)} Cr`;
  };

  return `
  <div class="section-card sc-green" id="fiidii-summary-card" style="min-width:0;">
    <button class="section-header nav-card-header" onclick="openFiiDiiModal()"
       aria-label="Open FII/DII cash-flow and participant-positioning detail" title="Open full FII/DII report">
      <span class="section-title nav-card-header-label"><span class="section-icon">🏦</span>FII / DII Cash Flow <span class="section-sub">Cash market</span></span>
      <span style="font-size:10px;color:var(--txt3);">${dateLabel}</span>
      <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
    </button>
    ${hasCash ? `
      <div class="fd-cash-summary">
        <div class="fd-cash-cell"><span class="k">FII</span><span class="v" style="color:${signColor(cash.fiiLatest,'var(--txt3)')};">${cashVal(cash.fiiLatest)}</span></div>
        <div class="fd-cash-cell"><span class="k">DII</span><span class="v" style="color:${signColor(cash.diiLatest,'var(--txt3)')};">${cashVal(cash.diiLatest)}</span></div>
        <div class="fd-cash-cell"><span class="k">Combined</span><span class="v" style="color:${signColor(cash.netLatest,'var(--txt3)')};">${cashVal(cash.netLatest)}</span></div>
        <div class="fd-cash-cell"><span class="k">Streak</span><span class="v">${cash.streakDays||0}d ${cash.streakDirection||'flat'}</span></div>
      </div>
      ${bias.overallLabel ? `<div class="legend-foot" style="margin-top:10px;"><b>Combined cash + F&amp;O context:</b> ${bias.overallLabel} · confidence ${fmtN(bias.overallConfidence||0,0)}%. Participant OI/divergence detail remains Tier 3.</div>` : `<div class="legend-foot" style="margin-top:10px;">Participant F&amp;O OI and divergence detail remain Tier 3.</div>`}
    ` : `<div class="dd-empty">Awaiting FII/DII cash-market flow history. Participant F&amp;O detail remains available in the full report when published.</div>`}
  </div>`;
}

  // ── FII / DII FULL TABLE MODAL ──
  // Writes the existing full comparison table (buildFiiDiiCard() below,
  // logic untouched) straight into #fiidii-modal-content — same pattern as
  // ChainView.renderGreeksGex() writing into #grkgex-content. Called from
  // _rerenderChainPanels() on every render/tick (chain-views.js) so the
  // modal is always current the moment it's opened, and again from
  // ModalManager.openFiiDiiModal() so a stale-until-next-tick state can
  // never be seen right after opening.
  renderFiiDiiModal(d){
  const el = $i('fiidii-modal-content');
  if(!el) return;
  el.innerHTML = this.buildFiiDiiCard(d);
}

  buildFiiDiiCard(d){
  // d.fiiDiiSentiment comes from fii_dii_sentiment.get_feature_for_trading_day()
  // via mTerminals_json.py — a flat dict, prior-trading-day EOD data, lagged
  // one session (never same-day) to avoid lookahead. {} until the first
  // post-close EOD fetch has run at least twice (needs 2 days to diff).
  const s = d.fiiDiiSentiment || {};
  const hasData = s && s.source_date;

  if(!hasData){
    return `
  <div class="exec-card c-fiidii" style="grid-column:1/-1;">
    <div class="exec-title">🏦 FII / DII / Pro / Retail Participant OI</div>
    <div class="dd-empty">Awaiting EOD participant-OI feed — populates after the first two post-close fetches.</div>
  </div>`;
  }

  const sentColor = (tag) => {
    if(!tag) return 'var(--txt3)';
    if(tag.includes('Bullish')) return 'var(--green)';
    if(tag.includes('Bearish')) return 'var(--red)';
    if(tag==='Mixed') return 'var(--amber)';
    return 'var(--txt3)';
  };

  // fmtSigned now hoisted to module scope above class ExecView.

  // ── Comparison table: one row per metric, one column per participant ──
  // (was one column per participant with all 4 metrics repeated inside
  // each — same numbers, 3x the vertical scan distance to compare e.g.
  // FII's PCR against DII's PCR. A shared header row + per-metric rows
  // means every cross-participant comparison is now a straight horizontal
  // read instead of jumping between separate boxes.)
  const participants = [
    { p: 'fii',    label: 'FII'    },
    { p: 'dii',    label: 'DII'    },
    { p: 'pro',    label: 'PRO'    },
    { p: 'retail', label: 'RETAIL' },
  ];

  // Mirrors _classify_sentiment()'s thresholds in fii_dii_sentiment.py so the
  // tooltip explains the tag with the exact same rule that produced it,
  // rather than a generic restatement. PCR rising = bearish tilt, PCR
  // falling = bullish tilt (opt_index_pcr's own convention) — Bullish
  // confirms on pcrChg <= 0, Bearish confirms on pcrChg >= 0.
  const sentReason = (p) => {
    const netChg = s[`${p}_index_fut_net_chg`];
    const pcrChg = s[`${p}_opt_index_pcr_chg`];
    if (netChg == null || pcrChg == null) return 'Insufficient data for classification.';
    const netTxt = `Net index-fut OI chg ${fmtSigned(netChg,0)}`;
    const pcrTxt = `Opt Index PCR chg ${fmtSigned(pcrChg,3)}`;
    if (netChg > 5000 && pcrChg <= 0)  return `${netTxt} (>+5,000) and ${pcrTxt} (≤0, more calls) → Bullish Build-up`;
    if (netChg < -5000 && pcrChg >= 0) return `${netTxt} (<-5,000) and ${pcrTxt} (≥0, more puts) → Bearish Build-up`;
    if (Math.abs(netChg) <= 5000)      return `${netTxt} (within ±5,000) → Neutral`;
    return `${netTxt}, ${pcrTxt} — OI change and PCR drift disagree on direction → Mixed`;
  };

  const headerRow = `
    <tr>
      <th class="dd-tbl-metric"></th>
      ${participants.map(({p,label}) => {
        const sentiment = s[`${p}_sentiment`];
        // "(Δ EOD)" clarifies this tag is a day-over-day CHANGE classification
        // (_classify_sentiment() in fii_dii_sentiment.py, off idx_fut_net_chg),
        // not a current positioning level — so it doesn't read as contradicting
        // the flow panel's gauge below, which shows FII's current long-share
        // LEVEL (see fdRenderRatioBar / fdGaugeSub in fiidii-report.js). The
        // two can legitimately disagree: e.g. FII can trim longs day-over-day
        // (Bearish Build-up here) while still sitting net-long overall
        // (Bullish Bias on the gauge).
        return `<th style="color:${sentColor(sentiment)};cursor:help;" title="${sentReason(p)}">${label}<br><span style="font-weight:400;font-size:0.85em;">${sentiment||'—'}${sentiment?'<sup style="font-size:8px;color:var(--txt3);margin-left:1px;">ℹ</sup>':''}</span><br><span style="font-weight:400;font-size:0.7em;color:var(--txt3);">(Δ vs prior EOD)</span></th>`;
      }).join('')}
    </tr>`;

  const metricRow = (metricLabel, valueFn, title) => `
    <tr title="${title||''}">
      <td class="dd-tbl-metric">${metricLabel}</td>
      ${participants.map(({p}) => `<td>${valueFn(p)}</td>`).join('')}
    </tr>`;

  const netOiRow = metricRow('Index Fut Net OI', (p) => {
    const net = s[`${p}_index_fut_net`];
    return `<strong style="color:var(--txt);">${fmtN(net,0)}</strong>`;
  });

  // Fixed: `${p}_index_opt_net` never existed in fii_dii_sentiment.py — the
  // backend only computes call and put net OI separately:
  //   opt_index_call_net = call_long - call_short
  //   opt_index_put_net  = put_long  - put_short
  // (confirmed against fii_dii_sentiment.py's _derived_metrics()). Shown
  // now as three rows: Call Net and Put Net on their own (so each side of
  // the option book is checkable against the raw NSE table directly),
  // then a combined Net OI row (Put − Call) underneath.
  const optCallNetRow = metricRow('Opt Index Call Net', (p) => {
    const net = s[`${p}_opt_index_call_net`] ?? 0;
    const chg = s[`${p}_opt_index_call_net_chg`] ?? 0;
    const clr = chg > 0 ? 'var(--green)' : chg < 0 ? 'var(--red)' : 'var(--txt3)';
    return `<strong style="color:var(--txt);">${fmtN(net,0)}</strong> <span style="color:${clr};font-size:0.8em;">(${fmtSigned(chg)})</span>`;
  }, 'Call long − call short; parenthetical is day-over-day change vs ' + (s.compare_date||'—'));

  const optPutNetRow = metricRow('Opt Index Put Net', (p) => {
    const net = s[`${p}_opt_index_put_net`] ?? 0;
    const chg = s[`${p}_opt_index_put_net_chg`] ?? 0;
    const clr = chg > 0 ? 'var(--green)' : chg < 0 ? 'var(--red)' : 'var(--txt3)';
    return `<strong style="color:var(--txt);">${fmtN(net,0)}</strong> <span style="color:${clr};font-size:0.8em;">(${fmtSigned(chg)})</span>`;
  }, 'Put long − put short; parenthetical is day-over-day change vs ' + (s.compare_date||'—'));

  // Day-over-day change combined the same way from the backend's own
  // opt_index_put_net_chg / opt_index_call_net_chg (chg is linear, so
  // put_chg - call_chg equals the chg of the combined figure).
  const optNetOiRow = metricRow('Index Opt Net OI (Put−Call)', (p) => {
    const putNet  = s[`${p}_opt_index_put_net`]  ?? 0;
    const callNet = s[`${p}_opt_index_call_net`] ?? 0;
    const putChg  = s[`${p}_opt_index_put_net_chg`]  ?? 0;
    const callChg = s[`${p}_opt_index_call_net_chg`] ?? 0;
    const net = putNet - callNet;
    const chg = putChg - callChg;
    const clr = chg > 0 ? 'var(--green)' : chg < 0 ? 'var(--red)' : 'var(--txt3)';
    return `<strong style="color:var(--txt);">${fmtN(net,0)}</strong> <span style="color:${clr};font-size:0.8em;">(${fmtSigned(chg)})</span>`;
  }, 'Put Net OI minus Call Net OI (both long − short); parenthetical is day-over-day change vs ' + (s.compare_date||'—'));

  const dayChgRow = metricRow('Day Chg', (p) => {
    const netChg = s[`${p}_index_fut_net_chg`];
    const clr = netChg > 0 ? 'var(--green)' : netChg < 0 ? 'var(--red)' : 'var(--txt3)';
    return `<strong style="color:${clr};">${fmtSigned(netChg)}</strong>`;
  }, `Change vs prior trading day (${s.compare_date||'—'})`);

  const ratioRow = metricRow('Long/Short Ratio', (p) => {
    const ratio    = s[`${p}_index_fut_long_short_ratio`];
    const ratioChg = s[`${p}_index_fut_long_short_ratio_chg`];
    return `<strong style="color:var(--txt);">${fmtN(ratio,2)}</strong> <span style="color:${ratioChg>=0?'var(--green)':'var(--red)'};font-size:0.8em;">(${fmtSigned(ratioChg,2)})</span>`;
  });

  const pcrRow = metricRow('Index Opt PCR', (p) => {
    const pcr = s[`${p}_opt_index_pcr`];
    return `<strong style="color:var(--txt);">${fmtN(pcr,2)}</strong>`;
  });

  const divergent = !!s.fii_dii_divergence;
  const proDivergent = !!s.pro_vs_fii_dii_divergence;

  return `
  <div class="exec-card c-fiidii" style="grid-column:1/-1;">
    <div class="exec-title">🏦 FII / DII / Pro / Retail Participant OI <span style="font-weight:400;color:var(--txt3);font-size:0.75em;">— EOD ${s.source_date} vs ${s.compare_date||'—'}</span></div>
    <table class="dd-tbl">
      <thead>${headerRow}</thead>
      <tbody>
        ${netOiRow}
        ${optCallNetRow}
        ${optPutNetRow}
        ${optNetOiRow}
        ${dayChgRow}
        ${ratioRow}
        ${pcrRow}
      </tbody>
    </table>
    ${divergent ? `<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border);font-size:10px;color:var(--amber);">⚠ FII and DII index-future positioning diverged day-over-day — opposite-direction net OI change.</div>` : ''}
    ${proDivergent ? `<div style="margin-top:${divergent?'4px':'8px'};${divergent?'':'padding-top:8px;border-top:1px solid var(--border);'}font-size:10px;color:var(--amber);">⚠ Pro desk positioning diverged from combined FII+DII flow day-over-day — prop writers moved opposite the institutional flow.</div>` : ''}
  </div>`;
}

  progress(name,val,clr,tip){
  clr = clr || (val>=65?'var(--green)':val<=35?'var(--red)':'var(--amber)');
  const titleAttr = tip ? ` title="${tip}"` : '';
  return `
<div class="p-row"${titleAttr} style="${tip?'cursor:help;':''}">
  <span style="font-size:11px;color:var(--txt2);white-space:nowrap;">${name}${tip?'<sup style="font-size:8px;color:var(--txt3);margin-left:1px;">ℹ</sup>':''}</span>
  <div class="p-bar"><div class="p-fill" style="width:${val}%;background:${clr};"></div></div>
  <strong style="font-size:11px;font-family:var(--mono);color:${clr};">${val}</strong>
</div>
`;}

  signal(name,val){

const t=(val||"").toLowerCase();

const cls=t.includes("bull")
?"sig-bull"
:t.includes("bear")
?"sig-bear"
:"sig-neutral";

return `

<div class="sig-row">

<span>${name}</span>

<span class="${cls}">

${val||"--"}

</span>

</div>

`;

}
}