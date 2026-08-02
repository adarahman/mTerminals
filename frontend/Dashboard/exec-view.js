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

  // Market health scores — driven by real backend data
  // NOTE: Trend was removed from here — it was just re-deriving the same
  // number the Decision Engine box already shows as "Confidence" (dec.confidence),
  // so it was a pure duplicate rather than an independent signal.

  // Momentum: spot day-change % + futures basis nudge
  const basisNudge   = Math.max(-10, Math.min(10, Math.round((d.basis||0) / 5)));
  const momScore     = Math.max(10, Math.min(90, Math.round(50 + (d.spotChgPct||0) * 6 + basisNudge)));

  // OI Flow: blend total PCR + intraday OI-change PCR (d.oiChgPCR)
  const oiChgPcr     = d.oiChgPCR || pcr;
  const blendedPcr   = pcr * 0.5 + oiChgPcr * 0.5;
  const oiScore      = Math.max(10, Math.min(90,
                         Math.round(blendedPcr > 1 ? 50 + (blendedPcr-1)*30 : 50 - (1-blendedPcr)*30)));

  // Theta Burn: actual atmTheta from Black-Scholes blended with DTE pressure
  const thetaRaw     = Math.abs(d.atmTheta || 0);
  const thetaNorm    = Math.min(thetaRaw / 15, 1);           // 15pts/day → 100%
  const dtePressure  = Math.max(0, Math.min(1, 1 - (d.dte||7) / 10));
  const thetaScore   = Math.max(10, Math.min(90, Math.round((thetaNorm * 0.6 + dtePressure * 0.4) * 90)));

  return `
<div id="exec-section-wrap">
<div class="exec-grid">

  <!-- ── CARD 1: MARKET HEALTH + STORY (merged) ── -->
  <!-- Health's 3 progress bars and Story's narrative lines were two -->
  <!-- separate cards; merged into one per the updated layout, freeing -->
  <!-- the middle slot for Greeks/Net GEX (moved here from row2 so it -->
  <!-- sits directly left of the Option Chain Snapshot card). Max Pain -->
  <!-- lives in the Decision Engine's Verdicts column above — not -->
  <!-- repeated here. -->
  <div class="exec-card c-blue">
    <div class="exec-title">📊 Market Health &amp; Story</div>
    ${progress("Momentum",  momScore,   d.spotChgPct>=0?'var(--green)':'var(--red)')}
    ${progress("OI Flow",   oiScore,    oiScore>55?'var(--green)':oiScore<45?'var(--red)':'var(--amber)')}
    ${progress("Theta Burn", thetaScore, thetaScore>=70?'var(--red)':thetaScore>=45?'var(--amber)':'var(--green)', `DTE ${d.dte||0} · ATM Θ ${fmtN(d.atmTheta,2)}/day — higher = more theta decay pressure.`)}
    ${(() => {
      // Expected move is approximated from the ATM straddle premium (CE+PE).
      // Using the SAME sum here and in the line below keeps the two numbers
      // consistent — previously ±Move pulled an unrelated d.straddle field
      // that could drift out of sync with the CE/PE premiums shown beneath it.
      const atmStraddlePrem = (d.callPremium||0) + (d.putPremium||0);
      // The Decision Engine's actual recommended structure (may not be a straddle
      // at all — e.g. Bull Call Spread, Iron Condor). Show it explicitly instead
      // of letting the "Straddle" label imply that's the recommended trade.
      const stratName = dec.autoStrategy?.name || null;
      return `
    <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border);">
      <div class="story">±Expected Move <strong style="color:var(--blue);">${Math.round(atmStraddlePrem)}</strong></div>
      <div class="story">ATM Straddle Prem <strong style="color:var(--txt);">CE ₹${fmtN(d.callPremium||0,1)} + PE ₹${fmtN(d.putPremium||0,1)}</strong></div>
      ${stratName ? `<div class="story">Engine Pick <strong style="color:var(--amber);">${stratName}</strong></div>` : ''}
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border);font-size:10px;color:var(--txt3);line-height:1.6;">
        ${isBull ? '🟢 Put writing continues — buy dips.' : isBear ? '🔴 Call writing heavy — sell rallies.' : '🟡 Mixed signals — wait for breakout.'}
      </div>
    </div>
      `;
    })()}
  </div>

  <!-- ── CARD 2: GREEKS / NET GEX ── -->
  <!-- Moved here from row2 (chain-renderer.js's Tier-2 row) so it sits
       immediately left of the Option Chain Snapshot card in the same row,
       per the updated layout. activeAtm()/getFilteredChain() are the same
       global helpers chain-renderer.js uses ahead of this same call. -->
  ${(() => {
    const gAtm = activeAtm(d);
    const gChain = getFilteredChain(d);
    const gStrikeSet = new Set(gChain.map(r=>r.strike));
    const gGreeks = (d.greeks||[]).filter(g=>gStrikeSet.has(g.strike));
    return app.chain.buildGreeksAlertsHtml(gGreeks, gAtm, d);
  })()}

  <!-- ── CARD 3: OPTION CHAIN SNAPSHOT ── -->
  <!-- Top Movers (Drivers/Draggers) moved out of this slot — pending its
       own standalone section/customization elsewhere; buildDriversDraggersCard()
       below is kept intact for that, just no longer called from here.
       app.chain is the live ChainView instance (see dashboard.js), so this
       reuses the exact same builder/markup as the row2 Snapshot card. -->
  ${app.chain.buildChainSummaryHtml(d)}

</div>

<!-- FII/DII Sentiment used to render here, full-width below the exec
     grid. It now lives in row2 (chain-renderer.js), grouped with Chain
     Snapshot and Greeks Alerts per the redesign mockup's Tier-2 layout —
     see chain-renderer.js's renderDashboard() and layout.css's .row2. -->
<!-- Institutional Activity Crux used to render here too, full-width
     below FII/DII. Moved down to sit directly beside the Vol/OI
     Velocity + Strike Detail panel (chain-renderer.js's
     buildSimulatorHtml) that its "Strike Detail →" button expands,
     instead of being separated from it by the whole page. -->
</div>
`;}

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
  const greeksData = d.greeks || [];
  const ratios = d.volOiRatios || {};
  const atm = d.atm || (d.ctx && d.ctx.atm) || 0;
  const step = greeksData.length > 1 ? (greeksData[1].strike - greeksData[0].strike) : 50;

  if(!chain.length || !atm){
    return `
  <div class="oic-card" id="inst-activity-summary-card">
    <div class="oic-head">
      <div class="oic-head-left">
        <span class="oic-icon icon-amber">🏛️</span>
        <span class="oic-title">Institutional Activity Crux</span>
      </div>
    </div>
    <div class="oic-empty">Awaiting chain data…</div>
    <div class="oic-footer"><button class="oic-action" onclick="openStrikeDetailReportModal()" title="Open Strike Detail report">📄 Strike Detail Report →</button></div>
  </div>`;
  }

  // Median OI here is computed across the FULL chain, not the table's
  // 10-nearest-strikes window, so the crux reflects the whole book.
  const oiTotals = chain.map(r => (r.ceOI||0) + (r.peOI||0)).sort((a,b) => a-b);
  const medianOI = oiTotals.length ? oiTotals[Math.floor(oiTotals.length/2)] : 0;

  const flagged = [];
  chain.forEach(r => {
    const rawRatio = ratios[String(r.strike)];
    if(!rawRatio) return; // missing data never counts as institutional
    const totalOI = (r.ceOI||0) + (r.peOI||0);
    const volRatio = totalOI > 0 ? ((rawRatio.ce||0) + (rawRatio.pe||0)) / 2 : 0;
    const band = instBandFor(r.strike, atm, step);
    const th = INST_THRESHOLDS[band];
    if(!(totalOI > medianOI * th.oiMult && volRatio < th.volRatioMax)) return;
    const oiDominant = (r.ceOI||0) >= (r.peOI||0) ? 'CE' : 'PE';
    // Strength is scored relative to each band's own bar, so a far-band
    // strike that just clears its (lower) bar doesn't automatically
    // outrank a near-band strike clearing its (higher) bar decisively.
    const strength = totalOI / (medianOI * th.oiMult);
    flagged.push({ strike: r.strike, band, oiDominant, totalOI, volRatio, strength });
  });

  const nearCount = flagged.filter(f => f.band==='near').length;
  const farCount  = flagged.filter(f => f.band==='far').length;
  const ceCount   = flagged.filter(f => f.oiDominant==='CE').length;
  const peCount   = flagged.filter(f => f.oiDominant==='PE').length;

  let biasLabel = 'Balanced', biasColor = 'var(--txt3)';
  if(ceCount > peCount){ biasLabel = 'CE-heavy (bearish tilt)'; biasColor = 'var(--red)'; }
  else if(peCount > ceCount){ biasLabel = 'PE-heavy (bullish tilt)'; biasColor = 'var(--green)'; }

  const top = flagged.slice().sort((a,b) => b.strength - a.strength)[0];

  // Bias badge reuses --pos/--neg (not a dedicated purple) since CE-heavy/
  // PE-heavy here is a real bull/bear tilt read, same convention as the
  // rest of the dashboard's bias colors — unlike the tile icons above,
  // which are just accent chips with no directional meaning.
  const biasBadgeClass = ceCount > peCount ? 'b-red' : peCount > ceCount ? 'b-green' : 'b-blue';
  const signalClr = top && top.oiDominant==='CE' ? 'var(--neg)' : 'var(--pos)';

  return `
  <div class="oic-card" id="inst-activity-summary-card">
    <div class="oic-head">
      <div class="oic-head-left">
        <span class="oic-icon icon-amber">🏛️</span>
        <span class="oic-title">Institutional Activity Crux <span class="oic-sub">ATM ±${INST_NEAR_BAND_STRIKES} strikes • near band</span></span>
      </div>
    </div>
    ${flagged.length===0 ? `
    <div class="oic-empty">No strikes currently clear the institutional threshold.</div>
    ` : `
    <div class="oic-badges">
      <span class="oic-badge b-blue">
        <span class="lbl">NEAR</span>
        <span class="val">${nearCount} flagged</span>
      </span>
      <span class="oic-badge b-amber">
        <span class="lbl">FAR</span>
        <span class="val">${farCount} flagged</span>
      </span>
      <span class="oic-badge ${biasBadgeClass}">
        <span class="lbl">BIAS</span>
        <span class="val" style="color:${biasColor};">${biasLabel}</span>
      </span>
    </div>
    ${top ? `
    <div class="oic-signal">
      <span class="oic-signal-icon">🎯</span>
      <div class="oic-signal-body">
        Strongest signal: <strong style="color:${signalClr};">${fmtI(top.strike)} ${top.oiDominant}</strong>
        <span class="oic-signal-meta">(${top.band} band · OI ${fmtK(top.totalOI)} · turnover ${fmtN(top.volRatio,1)}%)</span>
      </div>
    </div>` : ''}
    `}
    <div class="oic-footer"><button class="oic-action" onclick="openStrikeDetailReportModal()" title="Open Strike Detail report">📄 Strike Detail Report →</button></div>
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
  // ── FII / DII BIAS SUMMARY BLOCK (main-dashboard, not modal-only) ──
  // d.fiiDiiBias is analytics/fii_dii_market_bias.py's get_market_bias_report()
  // output verbatim — same shape/source fiidii-report.js's fdRenderBias()
  // renders inside the modal (backend/mTerminals_json.py now caches +
  // exposes it on the main tick as "fiiDiiBias" specifically so this read
  // doesn't depend on the modal's live /dashboard-relay connection). Reuses
  // the modal's .fd-bias-* classes (fiidii-report.css, loaded globally, not
  // modal-scoped) rather than duplicating that CSS here.
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
  const s = d.fiiDiiSentiment || {};
  const hasData = s && s.source_date;
  const biasHtml = this.buildFiiDiiBiasHtml(d.fiiDiiBias);

  if(!hasData){
    return `
  <div class="section-card sc-green" id="fiidii-summary-card" style="min-width:0;">
    <div class="section-header">
      <span class="section-title"><span class="section-icon">🏦</span>FII / DII / Pro / Retail Sentiment</span>
      ${biasHtml ? `<button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="openFiiDiiModal()" title="Open full participant OI table">Full Table →</button>` : ''}
    </div>
    ${biasHtml}
    <div class="dd-empty">Awaiting EOD participant-OI feed — populates after the first two post-close fetches.</div>
  </div>`;
  }

  const participants = [
    { p: 'fii',    label: 'FII Index Fut'    },
    { p: 'dii',    label: 'DII Index Fut'    },
    { p: 'pro',    label: 'PRO Index Fut'    },
    { p: 'retail', label: 'RETAIL Index Fut' },
  ];

  // 4-across single row — the previous 2x2 grid used grid-template-
  // columns:1fr 1fr, which on this card's actual (near-full-page) width
  // stretched each quadrant into a mostly-empty cell with the label/value
  // pinned to the left edge. A single row of 4 narrower columns uses that
  // width proportionally instead of wasting it.
  const rows = `<div style="display:grid;grid-template-columns:repeat(4,1fr);">` + participants.map(({p,label}, i) => {
    const net = s[`${p}_index_fut_net`];
    const isLast = i === participants.length - 1;
    const bstyle = `padding:10px 14px;${isLast?'':'border-right:1px solid var(--border);'}`;
    return `<div style="${bstyle}">
      <div style="font-size:10px;color:var(--txt3);margin-bottom:4px;">${label}</div>
      <div style="font-size:16px;font-weight:700;color:${signColor(net,'var(--txt3)')};">${fmtSigned(net,0)}</div>
    </div>`;
  }).join('') + `</div>`;

  // Divergence flags now render as icon-badge rows — same treatment as
  // Greeks/Net GEX Alerts (chain-greeks.js's buildGreeksAlertsHtml) — with
  // the participant-pair readout kept as a bordered pill (matches the
  // mockup's "PRO ⇄ FII+DII" chip) rather than a plain number, since it's
  // a label pairing, not a magnitude.
  const alerts=[];
  if(s.fii_dii_divergence){
    alerts.push({icon:'⇄', clr:'var(--amber)', title:'DIVERGENCE', text:'FII and DII index-future positioning diverged day-over-day', num:'FII ⇄ DII'});
  }
  if(s.pro_vs_fii_dii_divergence){
    alerts.push({icon:'↗', clr:'var(--amber)', title:'DIVERGENCE', text:'Pro desk positioning diverged from combined FII+DII flow', num:'PRO ⇄ FII+DII'});
  }
  const alertRows = alerts.map((a,i)=>`
    <div style="display:flex;align-items:center;gap:12px;padding:10px 2px;${i<alerts.length-1?'border-bottom:1px solid var(--border);':''}">
      <div style="width:38px;height:38px;flex:0 0 38px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb, ${a.clr} 16%, transparent);border:1px solid color-mix(in srgb, ${a.clr} 45%, transparent);">
        <span style="font-size:17px;color:${a.clr};line-height:1;">${a.icon}</span>
      </div>
      <div style="flex:1;min-width:0;">
        <div style="font-size:11.5px;font-weight:700;color:${a.clr};letter-spacing:.03em;">${a.title}</div>
        <div style="font-size:11px;color:var(--txt3);line-height:1.4;margin-top:2px;">${a.text}</div>
      </div>
      <span style="font-size:12px;font-weight:700;color:var(--txt);padding:7px 14px;border:1px solid var(--border);border-radius:9px;background:var(--bg2);white-space:nowrap;flex:0 0 auto;">${a.num}</span>
    </div>`).join('');

  return `
  <div class="section-card sc-green" id="fiidii-summary-card" style="min-width:0;">
    <div class="section-header">
      <span class="section-title"><span class="section-icon">🏦</span>FII / DII / Pro / Retail Sentiment</span>
      <span style="font-size:10px;color:var(--txt3);">EOD ${s.source_date} vs ${s.compare_date||'—'}</span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="openFiiDiiModal()" title="Open full participant OI table">Full Table →</button>
    </div>
    ${biasHtml}
    <div style="padding:2px 0;">
      ${rows}
    </div>
    ${alertRows ? `<div style="display:flex;flex-direction:column;margin-top:8px;padding-top:2px;border-top:1px solid var(--border);">${alertRows}</div>` : ''}
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

  buildDriversDraggersCard(d){
  const contributors = d.contributors || [];

  const impactOf = c => c.pointImpact!=null ? c.pointImpact : (c.point_impact!=null ? c.point_impact : 0);
  const pctOf    = c => c.pctChange!=null ? c.pctChange : (typeof c.pct_change==='string' ? parseFloat(c.pct_change) : (c.pct_change||0));

  const ddRow = (c, i, positive) => {
    const pts = impactOf(c);
    const pct = pctOf(c);
    const clr = positive ? 'var(--green)' : 'var(--red)';
    return `<div class="dd-row">
      <span class="dd-rank">${i+1}</span>
      <span class="dd-sym">${c.symbol||'—'}</span>
      <span class="dd-pct" style="color:${clr};" title="${pct>=0?'+':''}${fmtN(pct,2)}% move">${pct>=0?'+':''}${fmtN(pct,2)}%</span>
      <span class="dd-pts" style="color:${clr};" title="${pts>=0?'+':''}${fmtN(pts,2)} index points">${pts>=0?'+':''}${fmtN(pts,2)}</span>
    </div>`;
  };

  const drivers  = contributors.filter(c=>impactOf(c) > 0).sort((a,b)=>impactOf(b)-impactOf(a)).slice(0,3);
  const draggers = contributors.filter(c=>impactOf(c) < 0).sort((a,b)=>impactOf(a)-impactOf(b)).slice(0,3);

  const driverBody  = contributors.length
    ? (drivers.length  ? drivers.map((c,i)=>ddRow(c,i,true)).join('')   : `<div class="dd-empty">No positive contributors</div>`)
    : `<div class="dd-empty">Awaiting live contributor feed…</div>`;
  const draggerBody = contributors.length
    ? (draggers.length ? draggers.map((c,i)=>ddRow(c,i,false)).join('') : `<div class="dd-empty">No negative contributors</div>`)
    : `<div class="dd-empty">Awaiting live contributor feed…</div>`;

  return `
  <div class="exec-card c-movers">
    <div class="exec-title">🚀📉 Top Movers</div>
    <div class="dd-split">
      <div class="dd-col">
        <div class="dd-subtitle c-driver"><span></span><span>Drivers ·</span><span>%</span><span>pts</span></div>
        ${driverBody}
      </div>
      <div class="dd-col">
        <div class="dd-subtitle c-dragger"><span></span><span>Draggers ·</span><span>%</span><span>pts</span></div>
        ${draggerBody}
      </div>
    </div>
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