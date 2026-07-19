class OiFlowView {
  constructor() {
    this.oiFlowMode = 'oi';
  }

  buildOiTopMoversStrip(chain, velByStrike, mode){
  let ceStrike=null, ceVal=0, peStrike=null, peVal=0;
  chain.forEach(r=>{
    let ceV, peV;
    if(mode==='vel'){
      const vr=velByStrike[r.strike]||{};
      ceV=vr.ceDOI||0; peV=vr.peDOI||0;
    }else if(mode==='oi'){
      ceV=r.ceOI||0; peV=r.peOI||0;
    }else{
      ceV=r.ceChgOI||0; peV=r.peChgOI||0;
    }
    if(ceStrike===null||ceV>ceVal){ceVal=ceV;ceStrike=r.strike;}
    if(peStrike===null||peV>peVal){peVal=peV;peStrike=r.strike;}
  });
  if(ceStrike===null && peStrike===null) return '';
  const lbl=mode==='oi'?'Biggest CE OI':mode==='vel'?`Biggest CE Vel (${_velWin}m)`:'Biggest CE build';
  const lblPe=mode==='oi'?'Biggest PE OI':mode==='vel'?`Biggest PE Vel (${_velWin}m)`:'Biggest PE build';
  const ceHtml = ceStrike!==null ? `<span style="color:var(--txt3);">${lbl} <span style="color:var(--red);font-weight:600;">${fmtI(ceStrike)} ▲${fmtK(ceVal)}</span></span>` : '';
  const peHtml = peStrike!==null ? `<span style="color:var(--txt3);">${lblPe} <span style="color:var(--green);font-weight:600;">${fmtI(peStrike)} ▲${fmtK(peVal)}</span></span>` : '';
  return [ceHtml, peHtml].filter(Boolean).join('<span style="color:var(--border);">|</span>');
}

  buildOiFlowRows(chain, atm, maxOI, velByStrike, velMax, mode){
  const BFLY_MAX=64;
  const maxDOI=Math.max(...chain.map(r=>Math.max(Math.abs(r.ceChgOI||0),Math.abs(r.peChgOI||0))),1);
  let html='';
  chain.forEach(r=>{
    let ceV,peV,maxV,ceClr,peClr,signed;
    if(mode==='chg'){
      ceV=r.ceChgOI||0; peV=r.peChgOI||0; maxV=maxDOI;
      ceClr=ceOiChgClr(ceV); peClr=sClr(peV); signed=true;
    }else if(mode==='vel'){
      const vr=velByStrike[r.strike]||{};
      ceV=vr.ceDOI!=null?vr.ceDOI:0; peV=vr.peDOI!=null?vr.peDOI:0; maxV=velMax;
      ceClr=ceOiChgClr(ceV); peClr=sClr(peV); signed=true;
    }else{
      ceV=r.ceOI||0; peV=r.peOI||0; maxV=maxOI;
      ceClr='var(--red)'; peClr='var(--green)'; signed=false;
    }
    const cW=Math.max(Math.round(Math.abs(ceV)/maxV*BFLY_MAX),3);
    const pW=Math.max(Math.round(Math.abs(peV)/maxV*BFLY_MAX),3);
    const ia=r.atm||r.strike===atm;
    const sPCR=r.ceOI>0?(r.peOI||0)/r.ceOI:0;
    const pcrClr=sPCR>1?'var(--green)':sPCR<1?'var(--red)':'var(--txt3)';
    const ceLbl=(signed&&ceV>=0?'+':'')+fmtK(ceV);
    const peLbl=(signed&&peV>=0?'+':'')+fmtK(peV);
    html+=`<div class="oi-bfly-wrap" style="${ia?'background:rgba(18,184,134,0.06);border-radius:4px;padding:3px 4px;':''}">
      <span class="oi-bfly-fig" style="text-align:right;color:${ceClr};">${ceLbl}</span>
      <div class="oi-bfly-ce-track"><div class="oi-ce-bar" style="width:${cW}px;background:${ceClr};"></div></div>
      <span class="oi-bfly-strike" style="${ia?'color:var(--green);font-weight:600;':''}">${fmtI(r.strike)}${ia?' ★':''}</span>
      <div class="oi-bfly-pe-track"><div class="oi-pe-bar" style="width:${pW}px;background:${peClr};"></div></div>
      <span class="oi-bfly-fig" style="text-align:left;color:${peClr};">${peLbl}</span>
      <span class="oi-bfly-pcr" style="color:${pcrClr};">(${fmtN(sPCR,2)})</span>
    </div>`;
  });
  return html;
}

  switchOiFlowTab(mode,el){
  _oiFlowMode=mode;
  const grp=el?el.closest('#oi-flow-tabs'):document.getElementById('oi-flow-tabs');
  if(grp){grp.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active-oif'));}
  if(el) el.classList.add('active-oif');
  const lbl=document.getElementById('oi-flow-label');
  if(lbl) lbl.textContent=oiFlowLabel(mode);
  _rerenderChainPanels();
}

  // Compact "OI Flow Snapshot" card — replaces the full strike-by-strike
  // butterfly table that used to live in #sec-oi-buildup. That full table
  // (same buildOiFlowRows()/buildOiTopMoversStrip() logic, same CE|Strike|PE
  // layout) now lives in the OI Dashboard's "Butterfly" tab (oi_dashboard.html
  // / oi-dashboard.js) so it can be viewed full-size without competing for
  // space with Greeks/GEX on the main dashboard. This card is the "glance"
  // version: biggest CE/PE build, the ATM strike's own OI/PCR read, and a
  // button straight into that Butterfly tab — same pattern as the Option
  // Chain Snapshot card that replaced the old inline chain table.
  buildOiFlowSummaryHtml(chain, atm, velByStrike){
  if(!chain || !chain.length){
    return `
  <div class="section-card" id="oi-flow-summary-card" style="min-width:0;">
    <div class="section-header"><span class="section-title">📈 OI Flow Snapshot</span></div>
    <div class="dd-empty">Awaiting chain data…</div>
  </div>`;
  }

  const atmRow = chain.find(r=>r.atm||r.strike===atm) || chain[Math.floor(chain.length/2)];
  const ceAtm = atmRow.ceOI||0, peAtm = atmRow.peOI||0;
  const pcrAtm = ceAtm>0 ? (peAtm/ceAtm) : 0;
  const pcrAtmClr = pcrAtm>1?'var(--green)':pcrAtm<1?'var(--red)':'var(--txt3)';

  const totalCe = chain.reduce((s,r)=>s+(r.ceOI||0),0);
  const totalPe = chain.reduce((s,r)=>s+(r.peOI||0),0);
  const oiTotal = totalCe+totalPe || 1;
  const pcr = totalPe/(totalCe||1);

  return `
  <div class="section-card" id="oi-flow-summary-card" style="min-width:0;">
    <div class="section-header">
      <span class="section-title">📈 OI Flow Snapshot</span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="openOIDashboardModal('butterfly')">Butterfly View →</button>
    </div>
    <div style="padding:10px 2px 4px;">
      <div style="height:6px;border-radius:999px;overflow:hidden;display:flex;background:var(--bg2);margin-bottom:8px;">
        <div style="width:${(totalPe/oiTotal)*100}%;background:linear-gradient(90deg,var(--green),transparent);"></div>
        <div style="width:${(totalCe/oiTotal)*100}%;background:linear-gradient(90deg,transparent,var(--red));"></div>
      </div>
      <div style="display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:13px;font-weight:700;flex-wrap:wrap;margin-bottom:10px;">
        <span style="color:var(--green);">${fmtK(totalPe)}</span>
        <span style="font-size:9px;color:var(--txt3);font-weight:400;">PE</span>
        <span style="background:rgba(245,166,35,.15);color:var(--amber);padding:2px 8px;border-radius:999px;font-size:11px;">PCR ${fmtN(pcr,2)}</span>
        <span style="font-size:9px;color:var(--txt3);font-weight:400;">CE</span>
        <span style="color:var(--red);">${fmtK(totalCe)}</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;">
          <div style="font-size:9px;color:var(--txt3);letter-spacing:.04em;margin-bottom:4px;">ATM ${fmtI(atmRow.strike)}</div>
          <div style="display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:12px;font-weight:700;">
            <span style="color:var(--red);">${fmtK(ceAtm)}</span>
            <span style="font-size:9px;color:var(--txt3);font-weight:400;">CE / PE</span>
            <span style="color:var(--green);">${fmtK(peAtm)}</span>
          </div>
          <div style="font-size:10px;color:${pcrAtmClr};margin-top:3px;">PCR ${fmtN(pcrAtm,2)}</div>
        </div>
        <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;">
          <div style="font-size:9px;color:var(--txt3);letter-spacing:.04em;margin-bottom:4px;">BIGGEST BUILD</div>
          <div style="font-size:10.5px;line-height:1.7;">${buildOiTopMoversStrip(chain, velByStrike, 'oi') || '<span style="color:var(--txt3);">—</span>'}</div>
        </div>
      </div>
    </div>
  </div>`;
}
}

class ExecView {
  renderExecutiveDashboard(d){
  // ── Use Decision Engine output if available ───────────────────────────────
  const dec       = d.decision || {};
  const decBias   = dec.bias || '';           // BULLISH | BEARISH | NEUTRAL | CONFLICTED

  const isBull = decBias === 'BULLISH' || (!(decBias) && (d.compositeBias||'').toLowerCase().includes('bull'));
  const isBear = decBias === 'BEARISH' || (!(decBias) && (d.compositeBias||'').toLowerCase().includes('bear'));

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
<div class="exec-grid" style="grid-template-columns:0.85fr 1fr 1.15fr;">

  <!-- ── CARD 1: MARKET HEALTH ── -->
  <div class="exec-card c-blue">
    <div class="exec-title">📊 Market Health</div>
    ${progress("Momentum",  momScore,   d.spotChgPct>=0?'var(--green)':'var(--red)')}
    ${progress("OI Flow",   oiScore,    oiScore>55?'var(--green)':oiScore<45?'var(--red)':'var(--amber)')}
    ${progress("Theta Burn", thetaScore, thetaScore>=70?'var(--red)':thetaScore>=45?'var(--amber)':'var(--green)', `DTE ${d.dte||0} · ATM Θ ${fmtN(d.atmTheta,2)}/day — higher = more theta decay pressure.`)}
  </div>

  <!-- ── CARD 2: MARKET STORY (Max Pain lives in the Decision Engine's -->
  <!-- Verdicts column above — not repeated here) ── -->
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
  <div class="exec-card c-amber">
    <div class="exec-title">📖 Market Story</div>
    <div class="story">±Expected Move <strong style="color:var(--blue);">${Math.round(atmStraddlePrem)}</strong></div>
    <div class="story">ATM Straddle Prem <strong style="color:var(--txt);">CE ₹${fmtN(d.callPremium||0,1)} + PE ₹${fmtN(d.putPremium||0,1)}</strong></div>
    <div class="story">GEX <strong style="color:var(--blue);">${d.totalGEX!=null?fmtN(d.totalGEX,2)+'B':'—'}</strong></div>
    ${stratName ? `<div class="story">Engine Pick <strong style="color:var(--amber);">${stratName}</strong></div>` : ''}
    <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border);font-size:10px;color:var(--txt3);line-height:1.6;">
      ${isBull ? '🟢 Put writing continues — buy dips.' : isBear ? '🔴 Call writing heavy — sell rallies.' : '🟡 Mixed signals — wait for breakout.'}
    </div>
  </div>
    `;
  })()}

  <!-- ── CARD 3: TOP MOVERS (drivers & draggers) ── -->
  ${buildDriversDraggersCard(d)}

</div>

<!-- FII / DII crux summary (rendered below exec grid). Full comparison
     table moved to its own modal (see ModalManager.openFiiDiiModal() /
     #fiidii-dashboard-modal in DashboardPro.html) so it no longer eats
     main-dashboard real estate on every rebuild — same treatment Greeks/
     GEX already got. This card is just the composite read + a link out. -->
${buildFiiDiiSummaryCard(d)}
</div>
`;}

  // ── FII / DII CRUX SUMMARY (main dashboard card) ──
  // Same alert-card visual language as ChainView.buildGreeksAlertsHtml():
  // a compact, always-visible read (composite per-participant sentiment tag
  // + any divergence flags) plus a "Full Table →" button that opens the
  // full comparison table in its own modal instead of rendering inline.
  buildFiiDiiSummaryCard(d){
  const s = d.fiiDiiSentiment || {};
  const hasData = s && s.source_date;

  if(!hasData){
    return `
  <div class="exec-card c-fiidii" style="grid-column:1/-1;">
    <div class="exec-title">🏦 FII / DII / Pro / Retail Sentiment</div>
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

  const participants = [
    { p: 'fii',    label: 'FII'    },
    { p: 'dii',    label: 'DII'    },
    { p: 'pro',    label: 'PRO'    },
    { p: 'retail', label: 'RETAIL' },
  ];

  const tags = participants.map(({p,label}) => {
    const sentiment = s[`${p}_sentiment`];
    return `<span style="display:flex;align-items:center;gap:5px;padding:5px 10px;background:rgba(255,255,255,0.03);border-left:2px solid ${sentColor(sentiment)};border-radius:4px;font-size:11px;">
      <strong style="color:var(--txt2);">${label}</strong>
      <span style="color:${sentColor(sentiment)};">${sentiment||'—'}</span>
    </span>`;
  }).join('');

  const divergent = !!s.fii_dii_divergence;
  const proDivergent = !!s.pro_vs_fii_dii_divergence;

  return `
  <div class="exec-card c-fiidii" style="grid-column:1/-1;">
    <div class="exec-title" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px;">
      <span>🏦 FII / DII / Pro / Retail Sentiment <span style="font-weight:400;color:var(--txt3);font-size:0.75em;">— EOD ${s.source_date} vs ${s.compare_date||'—'}</span></span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="openFiiDiiModal()" title="Open full participant OI table">Full Table →</button>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;">
      ${tags}
    </div>
    ${divergent ? `<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border);font-size:10px;color:var(--amber);">⚠ FII and DII index-future positioning diverged day-over-day — opposite-direction net OI change.</div>` : ''}
    ${proDivergent ? `<div style="margin-top:${divergent?'4px':'8px'};${divergent?'':'padding-top:8px;border-top:1px solid var(--border);'}font-size:10px;color:var(--amber);">⚠ Pro desk positioning diverged from combined FII+DII flow day-over-day — prop writers moved opposite the institutional flow.</div>` : ''}
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

  const fmtSigned = (v, dp=0) => {
    const n = Number(v)||0;
    return `${n>=0?'+':''}${fmtN(n,dp)}`;
  };

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

  const headerRow = `
    <tr>
      <th class="dd-tbl-metric"></th>
      ${participants.map(({p,label}) => {
        const sentiment = s[`${p}_sentiment`];
        return `<th style="color:${sentColor(sentiment)};">${label}<br><span style="font-weight:400;font-size:0.85em;">${sentiment||'—'}</span></th>`;
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

class StrategyView {
  constructor() {
    this.selStratIdx = 0;
  }

  renderStratPayoff(){
  if(!_data) return;
  const strats = _data.strategies || [];
  if(!strats.length) return;

  const stratSel  = document.getElementById('strat-select');
  const strikeSel = document.getElementById('strat-strike-select');
  if(!stratSel) return;

  const si   = parseInt(stratSel.value) || 0;
  const s    = strats[si];
  if(!s) return;

  // Populate strike dropdown on strategy change
  _populateStrikeDropdown(s);

  const spot     = _data.spot || _data.spotPrice || 0;
  const atm      = (_data.atm) || spot;
  const lotSize  = _data.lotSize || 50;
  const legs     = s.legs || [];

  // Determine base strike from dropdown (apply ATM offset to legs)
  const selectedStrike = strikeSel.value ? parseFloat(strikeSel.value) : atm;
  const atmDefault     = atm || selectedStrike;
  const offset         = selectedStrike - atmDefault;

  // Build shifted legs
  const shiftedLegs = legs.map(l=>({...l, strike:(l.strike||atmDefault)+offset}));

  // Net credit/debit
  let netVal = (s.netCredit !== undefined && s.netCredit !== null)
    ? parseFloat(s.netCredit)
    : legs.reduce((acc,l)=>acc+(l.action==='SELL'?parseFloat(l.ltp)||0:-(parseFloat(l.ltp)||0)),0);
  netVal = isNaN(netVal) ? 0 : netVal;
  const isCredit = netVal >= 0;

  // ── PAYOFF CURVE ──
  const range    = Math.max(atm * 0.05, 600);
  const center   = selectedStrike || spot || atm;
  const xMin     = center - range;
  const xMax     = center + range;
  const steps    = 200;
  const dx       = (xMax - xMin) / steps;
  let   yMin     = Infinity, yMax = -Infinity;
  const xs=[], ys=[];
  for(let i=0;i<=steps;i++){
    const x = xMin + i*dx;
    const y = shiftedLegs.reduce((acc,l)=>acc+_legPnl(l,x,lotSize),0);
    xs.push(x); ys.push(y);
    if(y<yMin) yMin=y; if(y>yMax) yMax=y;
  }

  // Breakevens — zero crossings
  const breakevens=[];
  for(let i=0;i<ys.length-1;i++){
    if((ys[i]<=0&&ys[i+1]>0)||(ys[i]>=0&&ys[i+1]<0)){
      const frac=-ys[i]/(ys[i+1]-ys[i]);
      breakevens.push(Math.round(xs[i]+frac*(xs[i+1]-xs[i])));
    }
  }

  // Max profit / loss (capped for display)
  const maxProfit = Math.max(...ys);
  const maxLoss   = Math.min(...ys);

  // ── METRICS CARDS ──
  const metricsEl = document.getElementById('strat-metrics');
  if(metricsEl){
    const rupee = (v) => `₹${v >= 0 ? '+' : ''}${fmtI(Math.round(v))}`;
    const beStr = breakevens.length ? breakevens.map(b => '₹' + fmtI(b)).join(', ') : '—';
    
    // Layout and style setups
    const cardStyle = `background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:8px 10px;`;
    const lbStyle   = `font-size:9px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; margin-bottom:4px;`;
    const vlStyle   = (c) => `font-size:18px; font-weight:800; color:${c}; font-family:var(--mono); letter-spacing:-.02em;`;
    
    setHtmlIfChanged(metricsEl, `
      <div style="${cardStyle}">
        <div style="${lbStyle}">Max Profit</div>
        <div style="${vlStyle('var(--green)')}">
          ${isFinite(maxProfit) && maxProfit < 1e8 ? rupee(maxProfit) : 'Unlimited'}
        </div>
      </div>
      <div style="${cardStyle}">
        <div style="${lbStyle}">Max Loss</div>
        <div style="${vlStyle('var(--red)')}">
          ${isFinite(maxLoss) && maxLoss > -1e8 ? rupee(maxLoss) : 'Unlimited'}
        </div>
      </div>
      <div style="${cardStyle}">
        <div style="${lbStyle}">Breakevens</div>
        <div style="font-size:13px; font-weight:800; color:var(--amber); font-family:var(--mono); line-height: 1.4;">${beStr}</div>
      </div>
      <div style="${cardStyle}">
        <div style="${lbStyle}">Spot</div>
        <div style="${vlStyle('var(--blue)')}">₹${fmtI(Math.round(spot || center))}</div>
      </div>`);
  }
  // ── CANVAS DRAW ──
  const canvas = document.getElementById('strat-payoff-canvas');
  if(!canvas) return;

  // HiDPI — only resets the canvas (which clears the 2D context and is
  // what caused the visible flash) when the on-screen size actually
  // changed; every other tick just redraws onto the existing surface.
  const W0 = canvas.parentElement.clientWidth - 28;
  const H0 = 280;
  const ctx = sizeCanvasIfChanged(canvas, W0, H0);
  const W = W0, H = H0;

  // Dark mode detection
  const isDark = window.matchMedia('(prefers-color-scheme:dark)').matches;
  const C = {
    bg       : isDark ? '#1E2028' : '#F1F3F5',
    grid     : isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
    zero     : isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)',
    axisLbl  : isDark ? '#6C757D' : '#868E96',
    spot     : '#339AF0',
    be       : '#FFD43B',
    profitFill:'rgba(32,201,151,0.15)',
    lossFill :'rgba(255,107,107,0.13)',
    line     : '#5BC0DE',
    lineGlow : isDark ? 'rgba(91,192,222,0.6)' : 'rgba(51,154,240,0.5)',
  };

  const PAD = {l:52, r:16, t:16, b:36};
  const PW = W - PAD.l - PAD.r;
  const PH = H - PAD.t - PAD.b;

  // Scale helpers
  const padY = (yMax - yMin) * 0.08 || 500;
  const yLo  = yMin - padY, yHi = yMax + padY;
  const toX  = (v) => PAD.l + (v - xMin)/(xMax - xMin) * PW;
  const toY  = (v) => PAD.t + (1 - (v - yLo)/(yHi - yLo)) * PH;
  const zeroY= toY(0);

  ctx.clearRect(0,0,W,H);

  // ── Grid lines ──
  const yTicks = 6;
  for(let i=0;i<=yTicks;i++){
    const yv = yLo + (yHi-yLo)*i/yTicks;
    const py = toY(yv);
    ctx.strokeStyle = Math.abs(yv)<(yHi-yLo)*0.03 ? C.zero : C.grid;
    ctx.lineWidth   = Math.abs(yv)<(yHi-yLo)*0.03 ? 1 : 0.7;
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(PAD.l,py); ctx.lineTo(W-PAD.r,py); ctx.stroke();
    // Y axis labels
    ctx.fillStyle   = C.axisLbl;
    ctx.font        = `10px 'JetBrains Mono',monospace`;
    ctx.textAlign   = 'right';
    ctx.textBaseline= 'middle';
    const label = Math.abs(yv)>=1000 ? (yv>=0?'+':'')+Math.round(yv/1000)+'k'
                                     : (yv>=0?'+':'')+Math.round(yv);
    ctx.fillText('₹'+label, PAD.l-6, py);
  }

  // X axis ticks
  const xTicks = 8;
  ctx.font = `9px 'JetBrains Mono',monospace`;
  ctx.textAlign='center'; ctx.textBaseline='top';
  for(let i=0;i<=xTicks;i++){
    const xv = xMin + (xMax-xMin)*i/xTicks;
    const px = toX(xv);
    ctx.strokeStyle=C.grid; ctx.lineWidth=0.7;
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(px,PAD.t); ctx.lineTo(px,H-PAD.b); ctx.stroke();
    ctx.fillStyle=C.axisLbl;
    ctx.fillText('₹'+fmtI(Math.round(xv)), px, H-PAD.b+5);
  }

  // ── Profit / loss fill areas ──
  // Profit fill (above zero)
  ctx.beginPath();
  ctx.moveTo(toX(xs[0]), zeroY);
  xs.forEach((x,i)=>{ const py=toY(ys[i]); ctx.lineTo(toX(x), py<zeroY?py:zeroY); });
  ctx.lineTo(toX(xs[xs.length-1]), zeroY);
  ctx.closePath();
  ctx.fillStyle = C.profitFill;
  ctx.fill();

  // Loss fill (below zero)
  ctx.beginPath();
  ctx.moveTo(toX(xs[0]), zeroY);
  xs.forEach((x,i)=>{ const py=toY(ys[i]); ctx.lineTo(toX(x), py>zeroY?py:zeroY); });
  ctx.lineTo(toX(xs[xs.length-1]), zeroY);
  ctx.closePath();
  ctx.fillStyle = C.lossFill;
  ctx.fill();

  // ── Zero line ──
  ctx.strokeStyle='rgba(255,255,255,0.18)'; ctx.lineWidth=1;
  ctx.setLineDash([4,3]);
  ctx.beginPath(); ctx.moveTo(PAD.l,zeroY); ctx.lineTo(W-PAD.r,zeroY); ctx.stroke();
  ctx.setLineDash([]);

  // ── Payoff curve with glow ──
  function drawCurve(lw, clr, shadow, blur){
    ctx.save();
    if(shadow){ ctx.shadowColor=shadow; ctx.shadowBlur=blur||8; }
    ctx.strokeStyle=clr; ctx.lineWidth=lw; ctx.lineJoin='round'; ctx.lineCap='round';
    ctx.beginPath();
    xs.forEach((x,i)=>{ const px=toX(x),py=toY(ys[i]); i===0?ctx.moveTo(px,py):ctx.lineTo(px,py); });
    ctx.stroke();
    ctx.restore();
  }
  drawCurve(4, C.lineGlow, C.lineGlow, 12);
  drawCurve(2, C.line);

  // ── Spot vertical dashed line ──
  if(spot && spot>=xMin && spot<=xMax){
    const sx=toX(spot);
    ctx.strokeStyle=C.spot; ctx.lineWidth=1.2; ctx.setLineDash([5,3]);
    ctx.beginPath(); ctx.moveTo(sx,PAD.t); ctx.lineTo(sx,H-PAD.b); ctx.stroke();
    ctx.setLineDash([]);
    // label
    ctx.fillStyle=C.spot; ctx.font=`bold 10px 'Inter',sans-serif`;
    ctx.textAlign='center'; ctx.textBaseline='top';
    ctx.fillText('Spot', sx, PAD.t+2);
  }

  // ── Breakeven markers ──
  breakevens.forEach(be=>{
    if(be<xMin||be>xMax) return;
    const bx=toX(be);
    ctx.strokeStyle=C.be; ctx.lineWidth=1; ctx.setLineDash([3,2]);
    ctx.beginPath(); ctx.moveTo(bx,PAD.t); ctx.lineTo(bx,H-PAD.b); ctx.stroke();
    ctx.setLineDash([]);
    // dot on zero
    ctx.fillStyle=C.be;
    ctx.beginPath(); ctx.arc(bx,zeroY,4,0,Math.PI*2); ctx.fill();
    // label
    ctx.font=`bold 9px 'JetBrains Mono',monospace`;
    ctx.textAlign='center'; ctx.textBaseline='bottom';
    ctx.fillText('₹'+fmtI(be), bx, zeroY-6);
  });

  // ── LEG PILLS ──
  const legsEl = document.getElementById('strat-legs-row');
  if(legsEl){
    // Pin label
    const nameTag=`<span style="font-size:11px;font-weight:800;color:var(--muted);margin-right:2px;">📌 ${s.name||'Strategy'}</span>`;
    // Execute-whole-strategy button — fires one place_order per leg through
    // the same ptDispatchOrder() path the option-chain quick popover and
    // the main panel's "Place Order" button already use, so confirmations/
    // toasts/pending rows/portfolio refresh all behave identically no
    // matter where the order originated.
    const execAllBtn = `<span onclick="ptExecuteStrategy()" title="Place all legs of this strategy as paper orders"
      style="cursor:pointer;font-size:10px;font-weight:800;padding:3px 9px;border-radius:5px;
      background:var(--accent,#3b82f6);color:#fff;margin-left:8px;">▶ Execute Strategy</span>`;
    const symbolForLegs = _data.symbol || '';
    const expiryForLegs = s.expiry || _data.expiry || '';
    // BUGFIX: this pill used to show the raw, unresolved expiryForLegs —
    // so a cached strategy suggestion still carrying a rolled-off date
    // (e.g. "24-Jun" after that expiry has already passed) displayed as
    // if it were a live, tradeable expiry, with nothing on screen hinting
    // that "▶ Execute Strategy" was about to submit a dead contract.
    // Resolve it the same way execution does (ptExecuteStrategy /
    // ptExecuteLeg both call ptResolveStrategyExpiry) so the pill always
    // reflects what will actually be sent to the backend.
    const expiryForLegsReal = ptResolveStrategyExpiry(expiryForLegs);
    // Per-leg expiry: prefer the leg's own `expiry` field if the backend
    // ever sends one (forward-compatible), else fall back to the
    // strategy-level expiry above. A calendar spread is DEFINED by its
    // legs having different expiries at the same strike — collapsing
    // everything to one expiry, or omitting it entirely, silently turns a
    // calendar spread into something else. Detect that mismatch and flag
    // it instead of hiding it.
    const legExpiries = shiftedLegs.map(l=>l.expiry || expiryForLegs);
    const uniqueExpiries = [...new Set(legExpiries.filter(Boolean))];
    const isMultiExpiry = uniqueExpiries.length > 1;
    const staleExpiryWarn = (expiryForLegs && expiryForLegsReal && expiryForLegs !== expiryForLegsReal)
      ? `<span title="Strategy expiry ${expiryForLegs} is no longer live — will execute against ${expiryForLegsReal} instead" style="font-size:10px;font-weight:800;padding:2px 7px;border-radius:4px;
         background:rgba(255,107,107,.16);color:var(--red,#ff6b6b);margin-left:6px;">⚠ Rolled → ${ptFmtExpiry(expiryForLegsReal)}</span>`
      : '';
    const expiryPill = expiryForLegsReal
      ? `<span title="${expiryForLegsReal}" style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;
         background:rgba(59,130,246,.15);color:var(--accent,#3b82f6);margin-left:6px;">📅 ${ptFmtExpiry(expiryForLegsReal)}</span>`
      : '';
    const multiExpiryWarn = isMultiExpiry
      ? `<span title="Legs use different expiries: ${uniqueExpiries.join(', ')}" style="font-size:10px;font-weight:800;padding:2px 7px;border-radius:4px;
         background:rgba(237,161,0,.16);color:var(--amber,#eda100);margin-left:6px;">⚠ Multi-expiry</span>`
      : '';
    const pillsHtml = shiftedLegs.map(l=>{
      const isBuy=l.action==='BUY';
      const ac=isBuy?'var(--green)':'var(--red)';
      const acBg=isBuy?'rgba(32,201,151,0.12)':'rgba(255,107,107,0.12)';
      const border=isBuy?'rgba(32,201,151,0.35)':'rgba(255,107,107,0.35)';
      const ltp=parseFloat(l.ltp)||0;
      const legType=(l.type||'').toUpperCase();
      const legExpiry = l.expiry || expiryForLegs;
      // Real date used for execution/pricing; legExpiry above stays as the
      // raw NEAR/FAR label for display purposes.
      const legExpiryReal = ptResolveStrategyExpiry(legExpiry);
      // Only show a per-leg expiry tag when it differs from the strategy's
      // headline expiry (calendar spreads) — otherwise the shared expiry
      // pill above already covers every leg and repeating it per pill
      // would just be noise.
      const legExpTag = (l.expiry && l.expiry !== expiryForLegs)
        ? `<span style="color:var(--amber,#eda100);font-size:9px;" title="${l.expiry} → ${legExpiryReal}">(${ptFmtExpiry(l.expiry)})</span>`
        : '';
      const execBtn = `<span onclick="ptExecuteLeg('${symbolForLegs}','${legExpiryReal}',${l.strike},'${legType}','${l.action}',${l.lots||1},${ltp})"
        title="Execute this leg as a paper order (expiry ${legExpiryReal||'—'})"
        style="cursor:pointer;font-size:9px;font-weight:800;padding:1px 5px;border-radius:4px;
        background:${ac};color:#0b0d12;margin-left:2px;">▶</span>`;
      return `<span style="display:inline-flex;align-items:center;gap:4px;
        padding:5px 10px;border-radius:6px;border:1px solid ${border};
        background:${acBg};font-family:var(--mono);font-size:11px;font-weight:700;">
        <span style="color:${ac};">${l.action}</span>
        <span style="color:var(--txt);">${fmtI(l.strike)} ${legType}</span>
        ${legExpTag}
        <span style="color:var(--muted);">@</span>
        <span style="color:${ac};">₹${fmtN(ltp,2)}</span>
        ${execBtn}
      </span>`;
    }).join('');
    setHtmlIfChanged(legsEl, nameTag + expiryPill + staleExpiryWarn + multiExpiryWarn + execAllBtn + pillsHtml);
  }
}

  _afterRenderStratPayoff(){
  // Small delay to let innerHTML settle
  setTimeout(()=>{
    if(document.getElementById('strat-select')) renderStratPayoff();
  }, 80);
}

  _populateStrikeDropdown(strat){
  const sel = document.getElementById('strat-strike-select');
  if(!sel) return;
  const strikes = (strat.legs||[]).map(l=>l.strike).filter(Boolean);
  const atm = (_data && _data.atm) || (strikes.length ? strikes[0] : 0);
  // unique sorted strikes from chain near ATM
  let chainStrikes = [];
  if(_data && _data.chain){
    chainStrikes = _data.chain.map(r=>r.strike).filter(Boolean).sort((a,b)=>a-b);
  } else if(strikes.length){
    // fallback: ±10 strikes around ATM in steps of 50
    const step = 50;
    for(let i=-10;i<=10;i++) chainStrikes.push(atm + i*step);
  }
  const selectedVal = (_selStrike!=null && chainStrikes.includes(_selStrike)) ? _selStrike : atm;
  // Same diff pattern as the global expiry <select>: only rebuild the
  // option list (which visibly flickers/resets on every rebuild) when the
  // strike list itself changed; otherwise just keep the current selection
  // in sync without touching the DOM.
  const optionsKey = chainStrikes.join('|') + '@' + atm;
  if(sel.dataset.optionsKey !== optionsKey){
    sel.innerHTML = chainStrikes.map(s=>{
      const label = s === atm ? `${fmtI(s)} (ATM)` : fmtI(s);
      return `<option value="${s}" ${s===selectedVal?'selected':''}>${label}</option>`;
    }).join('');
    sel.dataset.optionsKey = optionsKey;
  } else if(sel.value !== String(selectedVal)){
    sel.value = selectedVal;
  }
}
}

class SimulatorView {
  constructor() {
    this.simSpotOverride = null;
    this.simIvOverride = null;
    this.simVelOverride = null;
    this.simDealerOverride = null;
    this.simState = {
  spot: 0, iv: 15, vel: 1.2, dealerBias: 0,
  greeks: [], atm: 0, step: 50, volOiRatios: {}
};
  }

  simInit() {
  if (!_data) return;
  var d = _data;
  var ctx = d.ctx || {};
  // d.greeks/d.atm/d.spot/d.atmIV are the fields applyExpirySelection()
  // actually rewrites when the global expiry dropdown changes; d.ctx is a
  // static snapshot from the very first payload and never updates, so
  // falling back to it (instead of preferring it) is what let the whole
  // Institutional Simulator + Scenario Controls freeze on expiry switch.
  this.simState.greeks = d.greeks || [];
  this.simState.atm = d.atm || ctx.atm || 0;
  this.simState.spot = d.spot || ctx.spot || 0;
  this.simState.iv = parseFloat(d.atmIV || ctx.baseIv || 15);
  this.simState.step = this.simState.greeks.length > 1 ?
    (this.simState.greeks[1].strike - this.simState.greeks[0].strike) : 50;
  this.simState.volOiRatios = d.volOiRatios || {};
  this.simUpdate();
}

  // oninput fires on every pixel of slider drag; without coalescing, each
  // of those events triggered a canvas redraw + vol-grid rebuild + table
  // innerHTML rebuild. Collapsed to one _simUpdateNow() per animation
  // frame — still reads the live slider value when the frame fires, same
  // pattern as scheduleRender() elsewhere.
  simUpdate() {
  if (this._simUpdateScheduled) return;
  this._simUpdateScheduled = true;
  var self = this;
  requestAnimationFrame(function(){
    self._simUpdateScheduled = false;
    self._simUpdateNow();
  });
}

  _simUpdateNow() {
  var spotEl = document.getElementById('sim-spot-slider');
  var ivEl   = document.getElementById('sim-iv-slider');
  var velEl  = document.getElementById('sim-vel-slider');
  var selEl  = document.getElementById('sim-dealer-sel');
  if (!spotEl) return;

  // Fall back to last-known simState values (rather than throwing) if a
  // scenario-control element is missing from the current template — a
  // dropped control (e.g. the Vol/OI Velocity slider) should degrade that
  // one control, not blank the entire chart/vol-grid/table render.
  var simSpot = parseFloat(spotEl.value);
  var simIV   = ivEl  ? parseFloat(ivEl.value)  : (this.simState.iv  || 15);
  var simVel  = velEl ? parseFloat(velEl.value) : (this.simState.vel || 1.2);
  var simBias = selEl ? parseFloat(selEl.value) : (this.simState.dealerBias || 0);

  var spotValEl = document.getElementById('sim-spot-val');
  if (spotValEl) spotValEl.textContent = fmtI(Math.round(simSpot));
  var ivValEl = document.getElementById('sim-iv-val');
  if (ivValEl) ivValEl.textContent = fmtN(simIV, 1);
  var velValEl = document.getElementById('sim-vel-val');
  if (velValEl) velValEl.textContent = fmtN(simVel, 1);

  var ivRatio  = simIV / (this.simState.iv || simIV);
  var vannaAdj = 1.0 + Math.abs(simBias) * 0.15 * ivRatio;

  var simGEX = this.simState.greeks.map(function(g) {
    var adjGex = (g.netGEX || 0) * ivRatio * vannaAdj;
    // g.iv from the greeks payload is a decimal fraction (0.40 = 40%), unlike
    // every other IV field in this app (ceIV/peIV/atmIV are already percent,
    // e.g. 37.87). Convert here so simRenderTable's "fmtN(iv,1) + '%'" shows
    // real values instead of everything collapsing toward 0.x% after rounding.
    var ivPct = g.iv != null ? g.iv * 100 : null;
    return { strike: g.strike, netGEX: adjGex, iv: ivPct, cDelta: g.cDelta, pDelta: g.pDelta, cGamma: g.cGamma };
  });

  var totalGEX = simGEX.reduce(function(s, g) { return s + g.netGEX; }, 0);
  var vannaMultiplier = 1.0 + Math.abs(totalGEX) / (30 * ivRatio);
  var flipRow = findGammaFlipStrike(simGEX);

  var gexEl = document.getElementById('sim-stat-gex');
  if (gexEl) {
    gexEl.textContent = fmtN(totalGEX, 2);
    gexEl.style.color = totalGEX >= 0 ? 'var(--blue)' : 'var(--red)';
    var sub = gexEl.nextElementSibling;
    if (sub) sub.textContent = totalGEX >= 0 ? 'Long gamma (dampens)' : 'Short gamma (amplifies)';
  }
  var vannaEl = document.getElementById('sim-stat-vanna');
  if (vannaEl) vannaEl.textContent = fmtN(vannaMultiplier, 2);
  var flipEl = document.getElementById('sim-stat-flip');
  if (flipEl) flipEl.textContent = flipRow ? fmtI(flipRow.strike) : '--';

  var needlePct = Math.max(0, Math.min(100, 50 + (totalGEX / 25) * 50));
  var needle = document.getElementById('sim-regime-needle');
  if (needle) needle.style.left = needlePct.toFixed(1) + '%';
  var regimeVal = document.getElementById('sim-regime-val');
  if (regimeVal) {
    var label, color;
    if      (totalGEX >  10) { label = 'Long Gamma';  color = 'var(--green)'; }
    else if (totalGEX < -10) { label = 'Short Gamma'; color = 'var(--red)'; }
    else if (totalGEX >   2) { label = 'Mild Long';   color = 'var(--blue)'; }
    else if (totalGEX <  -2) { label = 'Mild Short';  color = 'var(--amber)'; }
    else                     { label = 'Balanced';     color = 'var(--txt2)'; }
    regimeVal.textContent = label;
    regimeVal.style.color = color;
  }

  this.simRenderGEXChart(simGEX, simSpot, flipRow ? flipRow.strike : 0);
  this.simRenderVolGrid(simGEX, simVel);
  this.simRenderTable(simGEX, simSpot, simIV);
}

  simRenderGEXChart(gexData, simSpot, flipStrike) {
  var canvas = document.getElementById('sim-gex-canvas');
  if (!canvas || !gexData.length) return;
  var W = canvas.parentElement.clientWidth - 28;
  var H = 220;
  // Same fix as the Strategy Payoff chart: only reset the canvas surface
  // (which clears the 2D context) when the on-screen size actually
  // changed, instead of doing it unconditionally on every live tick.
  var ctx = sizeCanvasIfChanged(canvas, W, H);

  var cs = getComputedStyle(document.documentElement);
  var clrBlue  = cs.getPropertyValue('--blue').trim()   || '#339AF0';
  var clrRed   = cs.getPropertyValue('--red').trim()    || '#FA5252';
  var clrBorder= cs.getPropertyValue('--border').trim() || 'rgba(0,0,0,0.07)';
  var clrTxt3  = cs.getPropertyValue('--txt3').trim()   || '#868E96';
  var clrGreen = cs.getPropertyValue('--green').trim()  || '#12B886';

  ctx.clearRect(0, 0, W, H);

  var PAD_L = 46, PAD_R = 12, PAD_T = 20, PAD_B = 36;
  var chartW = W - PAD_L - PAD_R;
  var chartH = H - PAD_T - PAD_B;

  var vals = gexData.map(function(g) { return g.netGEX; });
  var absVals = vals.map(function(v) { return Math.abs(v); });
  var maxV = Math.max.apply(null, absVals.concat([1]));
  var yRange = maxV * 1.25;

  // Grid lines
  var gridLines = 5;
  ctx.strokeStyle = clrBorder;
  ctx.lineWidth = 1;
  for (var gi = 0; gi <= gridLines; gi++) {
    var gy = PAD_T + (gi / gridLines) * chartH;
    ctx.beginPath(); ctx.moveTo(PAD_L, gy); ctx.lineTo(W - PAD_R, gy); ctx.stroke();
    var gv = yRange - (gi / gridLines) * yRange * 2;
    ctx.fillStyle = clrTxt3;
    ctx.font = '9px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(fmtN(gv, 1), PAD_L - 4, gy + 3);
  }

  var zeroY = PAD_T + (yRange / (yRange * 2)) * chartH;
  ctx.strokeStyle = clrBorder;
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(PAD_L, zeroY); ctx.lineTo(W - PAD_R, zeroY); ctx.stroke();

  var barW = Math.max(4, Math.floor((chartW / gexData.length) - 2));
  var barGap = chartW / gexData.length;

  for (var bi = 0; bi < gexData.length; bi++) {
    var g = gexData[bi];
    var bx = PAD_L + bi * barGap + (barGap - barW) / 2;
    var pct = g.netGEX / yRange;
    var barH = Math.abs(pct) * (chartH / 2);
    var by = g.netGEX >= 0 ? zeroY - barH : zeroY;
    ctx.fillStyle = g.netGEX >= 0 ? (clrGreen + 'AA') : (clrBlue + 'AA');
    ctx.strokeStyle = g.netGEX >= 0 ? clrGreen : clrBlue;
    ctx.lineWidth = 1;
    ctx.fillRect(bx, by, barW, Math.max(barH, 1));
    ctx.strokeRect(bx, by, barW, Math.max(barH, 1));
  }

  // Spot marker
  var spotIdx = 0;
  var minDist = Infinity;
  for (var si = 0; si < gexData.length; si++) {
    var d = Math.abs(gexData[si].strike - simSpot);
    if (d < minDist) { minDist = d; spotIdx = si; }
  }
  var spotX = PAD_L + spotIdx * barGap + barGap / 2;
  ctx.strokeStyle = clrBlue;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(spotX, PAD_T); ctx.lineTo(spotX, H - PAD_B); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = clrBlue;
  ctx.font = '9px Inter, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('SPOT:' + fmtI(Math.round(simSpot)), spotX, PAD_T - 4);

  // Flip zone
  if (flipStrike) {
    var fi = -1;
    for (var fii = 0; fii < gexData.length; fii++) {
      if (gexData[fii].strike === flipStrike) { fi = fii; break; }
    }
    if (fi >= 0) {
      var flipX = PAD_L + fi * barGap + barGap / 2;
      ctx.strokeStyle = clrRed;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(flipX, PAD_T); ctx.lineTo(flipX, H - PAD_B); ctx.stroke();
      ctx.fillStyle = clrRed;
      ctx.font = '9px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('FLIP ZONE', flipX, zeroY + 16);
    }
  }

  // X axis labels
  ctx.fillStyle = clrTxt3;
  ctx.font = '8px JetBrains Mono, monospace';
  ctx.textAlign = 'center';
  var labelStep = Math.max(1, Math.floor(gexData.length / 12));
  for (var li = 0; li < gexData.length; li++) {
    if (li % labelStep === 0) {
      var lx = PAD_L + li * barGap + barGap / 2;
      ctx.fillText(String(gexData[li].strike), lx, H - PAD_B + 14);
    }
  }
  ctx.fillStyle = clrTxt3;
  ctx.font = '9px Inter, sans-serif';
  ctx.textAlign = 'right';
  ctx.fillText('Strike \u2192', W - PAD_R, H - 2);

  // Tooltip
  canvas.onmousemove = function(e) {
    var rect = canvas.getBoundingClientRect();
    var mx = e.clientX - rect.left;
    var idx = Math.floor((mx - PAD_L) / barGap);
    var annot = document.getElementById('sim-annot');
    if (annot && idx >= 0 && idx < gexData.length) {
      var gp = gexData[idx];
      annot.style.display = 'block';
      annot.innerHTML = '<strong>Net GEX Profile ($B) \u2191 ' + fmtN(gp.netGEX, 2) + '</strong><br>Strike \u2192 ' + fmtI(gp.strike);
      annot.style.left = Math.min(mx + 10, W - 180) + 'px';
    }
  };
  canvas.onmouseleave = function() {
    var annot = document.getElementById('sim-annot');
    if (annot) annot.style.display = 'none';
  };
}

  simRenderVolGrid(gexData, simVel) {
  var el = document.getElementById('sim-vol-grid');
  if (!el) return;
  var ratios = this.simState.volOiRatios || {};
  var atm = this.simState.atm;

  // Same per-strike OI lookup used by the Strike Detail table below, so
  // the ratio bars up here and the OI figures down there always agree.
  var oiByStrike = {};
  ((_data && _data.chain) || []).forEach(function(r) {
    oiByStrike[r.strike] = { ce: r.ceOI || 0, pe: r.peOI || 0 };
  });

  var sorted = gexData.slice().sort(function(a, b) {
    return Math.abs(a.strike - atm) - Math.abs(b.strike - atm);
  });
  var near = sorted.slice(0, 8);

  var ceRows = near.map(function(g) {
    var r = ratios[String(g.strike)] || { ce: 0 };
    var oi = (oiByStrike[g.strike] || {}).ce || 0;
    return { strike: g.strike, val: (r.ce || 0) * simVel, oi: oi };
  }).sort(function(a, b) { return b.val - a.val; });

  var peRows = near.map(function(g) {
    var r = ratios[String(g.strike)] || { pe: 0 };
    var oi = (oiByStrike[g.strike] || {}).pe || 0;
    return { strike: g.strike, val: (r.pe || 0) * simVel, oi: oi };
  }).sort(function(a, b) { return b.val - a.val; });

  var maxCE = Math.max.apply(null, ceRows.map(function(r) { return r.val; }).concat([0.01]));
  var maxPE = Math.max.apply(null, peRows.map(function(r) { return r.val; }).concat([0.01]));

  function barRow(strike, val, max, color, oi) {
    var pct = Math.min(Math.round((val / max) * 100), 100);
    var isBlock = val > 1.5;
    return '<div class="sim-vol-bar-row">' +
      '<span class="sim-vol-bar-label" style="color:var(--txt2);">' + fmtI(strike) + '</span>' +
      '<div class="sim-vol-bar-track"><div class="sim-vol-bar-fill" style="width:' + pct + '%;background:' + color + ';"></div></div>' +
      '<span class="sim-vol-bar-val" style="color:' + (isBlock ? 'var(--amber)' : 'var(--txt3)') + ';">' + fmtN(val, 2) + (isBlock ? ' &#9650;' : '') + '</span>' +
      '<span class="sim-vol-bar-oi" style="color:var(--txt3);font-size:9px;margin-left:6px;white-space:nowrap;">OI ' + fmtK(oi) + '</span>' +
      '</div>';
  }

  var ceHtml = '<div class="sim-vol-card"><div class="sim-vol-card-title" style="color:var(--red);">CE Vol/OI Ratio</div>';
  ceRows.slice(0, 5).forEach(function(r) { ceHtml += barRow(r.strike, r.val, maxCE, 'var(--red)', r.oi); });
  ceHtml += '</div>';

  var peHtml = '<div class="sim-vol-card"><div class="sim-vol-card-title" style="color:var(--green);">PE Vol/OI Ratio</div>';
  peRows.slice(0, 5).forEach(function(r) { peHtml += barRow(r.strike, r.val, maxPE, 'var(--green)', r.oi); });
  peHtml += '</div>';

  setHtmlIfChanged(el, ceHtml + peHtml);
}

  simRenderTable(gexData, simSpot, simIV) {
  var el = document.getElementById('sim-strike-table');
  if (!el) return;
  var atm = this.simState.atm;
  var ratios = this.simState.volOiRatios || {};
  // Real per-strike open interest lives on the chain rows (ceOI/peOI), not
  // on volOiRatios (which only carries ce_vol/pe_vol — traded volume — plus
  // the vol/OI ratio itself). The table was previously showing
  // ceVol+peVol under the "Open Interest" header, which is volume, not OI.
  var oiByStrike = {};
  ((_data && _data.chain) || []).forEach(function(r) {
    oiByStrike[r.strike] = { ce: r.ceOI || 0, pe: r.peOI || 0 };
  });

  var sorted = gexData.slice().sort(function(a, b) {
    return Math.abs(a.strike - atm) - Math.abs(b.strike - atm);
  });
  var near = sorted.slice(0, 10);
  near.sort(function(a, b) { return b.strike - a.strike; });

  // "Institutional" should mean "large resting size relative to the other
  // strikes on screen right now" — a flat magic number (previously 5000)
  // is off by orders of magnitude vs. real OI (tens of thousands to
  // millions), so it was true for almost every strike regardless of actual
  // positioning. Use the median totalOI across the visible strikes instead,
  // so the label actually discriminates within whatever data is loaded.
  var oiTotals = near.map(function(g) {
    var s = oiByStrike[g.strike] || { ce: 0, pe: 0 };
    return s.ce + s.pe;
  }).sort(function(a, b) { return a - b; });
  var midIdx = Math.floor(oiTotals.length / 2);
  var medianOI = oiTotals.length ? oiTotals[midIdx] : 0;

  var html = '';
  near.forEach(function(g) {
    var isAtm = g.strike === atm;
    var rawRatio = ratios[String(g.strike)];
    var hasRatioData = !!rawRatio;
    var ratio = rawRatio || { ce: 0, pe: 0, ce_vol: 0, pe_vol: 0 };
    var ceVol = ratio.ce_vol || 0;
    var peVol = ratio.pe_vol || 0;
    var totalVol = ceVol + peVol;
    var oiSplit = oiByStrike[g.strike] || { ce: 0, pe: 0 };
    var totalOI = oiSplit.ce + oiSplit.pe;
    var volRatio = totalOI > 0 ? ((ratio.ce || 0) + (ratio.pe || 0)) / 2 : 0;
    // volRatio is on the same scale as the CE/PE Vol/OI Ratio panel above
    // (roughly 0-100+, "volume as % of OI"), not a 0-1 fraction. Large
    // resting size (OI well above the pack's median) plus low turnover
    // (< half of OI traded today) reads as institutional accumulation;
    // if we never received a ratio for this strike, that's missing data,
    // not a "0% turnover" reading, so it must not default into the
    // institutional branch.
    var isInst = hasRatioData && totalOI > medianOI * 1.5 && volRatio < 50;
    var actLabel = !hasRatioData ? 'No Data' : (isInst ? 'Institutional Accumulation' : 'Retail Flow');
    var netDelta = Math.abs((g.cDelta || 0) - Math.abs(g.pDelta || 0));

    var oiDominant = oiSplit.ce >= oiSplit.pe ? 'CE' : 'PE';
    var oiDomClr = oiDominant === 'CE' ? 'var(--red)' : 'var(--green)';

    html += '<div class="sim-table-row' + (isAtm ? ' atm-row' : '') + '" style="font-size:11px;">' +
      '<span style="font-family:var(--mono);font-weight:' + (isAtm ? 700 : 400) + ';color:' + (isAtm ? 'var(--txt)' : 'var(--txt2)') + ';">' + fmtI(g.strike) + '</span>' +
      '<span style="color:var(--txt2);font-family:var(--mono);white-space:nowrap;">' +
        fmtK(totalOI) + ' ' +
        '<span style="font-size:9px;color:' + oiDomClr + ';">(' + oiDominant + ' ' + fmtK(oiDominant === 'CE' ? oiSplit.ce : oiSplit.pe) + ')</span>' +
      '</span>' +
      '<span style="text-align:right;color:var(--amber);font-family:var(--mono);">' + fmtN(g.iv || simIV, 1) + '%</span>' +
      '<span style="text-align:right;font-family:var(--mono);color:var(--txt);">' + fmtN(netDelta, 2) + '</span>' +
      '<span style="text-align:right;display:flex;align-items:center;justify-content:flex-end;gap:4px;">' +
        '<span class="sim-inst-dot ' + (isInst ? 'filled' : '') + '"></span>' +
        '<span style="color:' + (isInst ? 'var(--green)' : 'var(--txt3)') + ';">' + actLabel + '</span>' +
      '</span>' +
    '</div>';
  });

  setHtmlIfChanged(el, html || '<div style="padding:12px;color:var(--txt3);font-size:11px;">No strike data available.</div>');
}
}

class ModalManager {
  constructor() {
    this.oiDashboardWin = null;
    this.oiFrameLoaded = false;
  }

  openOIDashboardModal(tab){
  if(location.protocol === 'file:'){
    _openOIDashboardPopupFallback(tab);
    return;
  }
  var modal = document.getElementById('oi-dashboard-modal');
  var frame = document.getElementById('oi-modal-iframe');
  if(!modal || !frame) return;
  if(!_oiFrameLoaded){
    frame.onload = function () {
        if (app.data.store.state) {
            frame.contentWindow.postMessage(
                { type: "OI_DASHBOARD_DATA", payload: app.data.store.state },
                "*"
            );
        }
        if (tab) {
            frame.contentWindow.postMessage({ type: "OI_DASHBOARD_SET_TAB", tab: tab }, "*");
        }
    };
    frame.src = 'oi_dashboard.html?v=' + Date.now() + (tab ? ('&tab=' + encodeURIComponent(tab)) : ''); // must sit alongside this file when deployed
    _oiFrameLoaded = true;
  }
  // Every time the modal opens, send the latest state so reopening
  // doesn't show stale data.
  if (frame.contentWindow && app.data.store.state) {
    frame.contentWindow.postMessage(
        { type: "OI_DASHBOARD_DATA", payload: app.data.store.state },
        "*"
    );
  }
  // Re-selecting the tab on every open (not just the first load) matters
  // because the iframe is created once and reused — a later call asking
  // for 'butterfly' after the panel already booted on 'oi' would otherwise
  // silently land on whatever tab was last active instead.
  if (tab && frame.contentWindow) {
    frame.contentWindow.postMessage({ type: "OI_DASHBOARD_SET_TAB", tab: tab }, "*");
  }
  modal.classList.add('open');
  document.addEventListener('keydown', _oiEscHandler);
}

  closeOIDashboardModal(){
  var modal = document.getElementById('oi-dashboard-modal');
  if(!modal) return;
  modal.classList.remove('open');
  document.removeEventListener('keydown', _oiEscHandler);
}

  _oiEscHandler(e){
  if(e.key === 'Escape') closeOIDashboardModal();
}

  // ── GREEKS / GEX MODAL ──
  // Unlike the OI Dashboard modal above, this isn't an iframe to a
  // separate document — the full Greeks/GEX table (renderGreeksGex() in
  // ChainView) already renders straight into #grkgex-content/#grkgex-footer,
  // which now live inside this modal's markup in DashboardPro.html instead
  // of inline in the main dashboard template. Those elements are never
  // destroyed by a dashboard rebuild (they're outside the rebuilt
  // #dashboard container), so they're kept continuously up to date by the
  // normal render/tick path whether or not the modal is currently open —
  // opening it is purely a visibility toggle, same chrome/Esc/backdrop
  // behavior as the OI Dashboard modal.
  openGreeksModal(){
  var modal = document.getElementById('greeks-dashboard-modal');
  if(!modal) return;
  modal.classList.add('open');
  document.addEventListener('keydown', _greeksEscHandler);
  // Refresh immediately on open too, in case something changed the
  // underlying data without a live tick firing in between (e.g. a
  // paste-load or an expiry switch made while the modal was closed).
  if(window.renderGreeksGex) renderGreeksGex(_grkView);
}

  closeGreeksModal(){
  var modal = document.getElementById('greeks-dashboard-modal');
  if(!modal) return;
  modal.classList.remove('open');
  document.removeEventListener('keydown', _greeksEscHandler);
}

  _greeksEscHandler(e){
  if(e.key === 'Escape') closeGreeksModal();
}

  _openOIDashboardPopupFallback(tab){
  if(_oiDashboardWin && !_oiDashboardWin.closed){
    _oiDashboardWin.focus();
    if (tab) _oiDashboardWin.postMessage({ type: "OI_DASHBOARD_SET_TAB", tab: tab }, "*");
    return;
  }
  var w = Math.min(1200, Math.round(screen.availWidth * 0.85));
  var h = Math.min(850, Math.round(screen.availHeight * 0.85));
  var left = Math.round((screen.availWidth - w) / 2);
  var top = Math.round((screen.availHeight - h) / 2);
  var features = 'width=' + w + ',height=' + h + ',left=' + left + ',top=' + top +
    ',resizable=yes,scrollbars=yes,toolbar=no,menubar=no,location=no,status=no';
  _oiDashboardWin = window.open('oi_dashboard.html?v=' + Date.now() + (tab ? ('&tab=' + encodeURIComponent(tab)) : ''), 'oiDashboardPopup', features);
  if(_oiDashboardWin) _oiDashboardWin.focus();
}

  // ── FII / DII MODAL ──
  // Same treatment as the Greeks modal above: plain in-page markup
  // (#fiidii-modal-content) that ExecView.renderFiiDiiModal() keeps
  // continuously current via _rerenderChainPanels (chain-views.js) on
  // every render/tick, whether or not this modal is open — opening is
  // purely a visibility toggle, same chrome/Esc/backdrop behavior as the
  // other two modals. Also refreshed right here on open in case something
  // changed the underlying data without a live tick firing in between.
  openFiiDiiModal(){
  var modal = document.getElementById('fiidii-dashboard-modal');
  if(!modal) return;
  modal.classList.add('open');
  document.addEventListener('keydown', _fiidiiEscHandler);
  if(app.data.store.state && app.exec.renderFiiDiiModal) app.exec.renderFiiDiiModal(app.data.store.state);
}

  closeFiiDiiModal(){
  var modal = document.getElementById('fiidii-dashboard-modal');
  if(!modal) return;
  modal.classList.remove('open');
  document.removeEventListener('keydown', _fiidiiEscHandler);
}

  _fiidiiEscHandler(e){
  if(e.key === 'Escape') closeFiiDiiModal();
}

  // ── IV SURFACE MODAL ──
  // Same treatment as the Greeks/FII-DII modals above: plain in-page
  // markup (#iv-surface-content) kept continuously current by
  // ChainView.renderIvSurfaceModal() via _rerenderChainPanels
  // (chain-views.js) on every render/tick, whether or not this modal is
  // open. openIvSurfaceModal() was already being called by
  // buildIvAlertsHtml()'s "Full Surface →" button, but this method itself
  // (and closeIvSurfaceModal/_ivSurfaceEscHandler) had never actually been
  // written, so that button threw a ReferenceError — root-cause fixed here.
  openIvSurfaceModal(){
  var modal = document.getElementById('iv-surface-modal');
  if(!modal) return;
  modal.classList.add('open');
  document.addEventListener('keydown', _ivSurfaceEscHandler);
  if(typeof app !== 'undefined' && app.chain && app.chain.renderIvSurfaceModal) app.chain.renderIvSurfaceModal();
}

  closeIvSurfaceModal(){
  var modal = document.getElementById('iv-surface-modal');
  if(!modal) return;
  modal.classList.remove('open');
  document.removeEventListener('keydown', _ivSurfaceEscHandler);
}

  _ivSurfaceEscHandler(e){
  if(e.key === 'Escape') closeIvSurfaceModal();
}
}

