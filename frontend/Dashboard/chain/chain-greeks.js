// ============================================================
// chain-greeks.js
// Phase 2 chain-view decomposition — see chain-view.js's header comment
// for the full split rationale and load-order requirement (this file
// must load after chain-view.js, and before dashboard.js).
//
// This file holds ChainView's Greeks/Net GEX rendering: the main-
// dashboard alerts summary, the ATM Greeks card, and the full per-strike
// Greeks & GEX table (the Greeks modal's content). Moved verbatim from
// chain-views.js.
// ============================================================

  // ── GREEKS ALERTS (main-dashboard summary card) ──
  // IA redesign step 2: scope tag added to the title so this card's raw,
  // un-adjusted totalGEX (summed off the live greeks array) reads as
  // distinct from the other two GEX-themed cards flagged in
  // dashboard-redesign-proposal.md §1's "Same story for Gamma/GEX"
  // paragraph — Advanced Analytics' GEX Table ("Top |GEX| Strikes",
  // per-strike ranked view) and the Institutional Simulator's Net GEX
  // Profile chart ("Scenario-Adjusted", vanna/IV-slider-adjusted, not the
  // live figure). Same underlying greeks array, three different
  // treatments of it.
  //
  // CORRECTION (step 6 audit): this card was labeled "Live, Whole-Chain"
  // but both call sites (exec-view.js's renderExecutiveDashboard and
  // chain-renderer.js's patchOuterHtmlIfChanged('greeks-alerts-card', ...))
  // actually pass it `greeks` filtered through getFilteredChain(d) — the
  // same user-controlled ±N strike range Range PCR uses, not the true
  // full chain (getFilteredChain only returns the unfiltered chain when
  // the range selector is set to "All", _chainRange===9999). Relabeled to
  // "Live, Visible Range" to match what's actually computed, same honesty
  // Range PCR already gets on the neighboring Chain Snapshot card,
  // instead of silently changing a live gamma/GEX alert's scope as a
  // side effect of a labeling fix. If true whole-chain GEX is wanted
  // here later, that's a deliberate follow-up (pass d.greeks unfiltered),
  // not this one.
  // The full per-strike Greeks/GEX table (Δ/Γ/Θ/Vega tabs + Net GEX +
  // Regime columns) moved out of the main dashboard into its own modal —
  // openGreeksModal()/closeGreeksModal() in ModalManager, mirroring the
  // existing OI Dashboard modal — so it never crowds the main view. What
  // stays inline here is just the handful of things worth reacting to:
  // a gamma-flip strike sitting inside the visible ATM range, a
  // short-gamma dealer regime (hedging flows amplify rather than dampen
  // moves), and unusually fast theta burn relative to the ATM straddle's
  // own premium. The %/day threshold below is a tunable heuristic — the
  // backend doesn't send an explicit "this is high" flag — not a value
  // pulled from the payload.
ChainView.prototype.buildGreeksAlertsHtml = function(greeks, atm, d) {
  const GREEKS_ALERT_THETA_PCT = 5; // ATM theta/day as % of ATM straddle premium
  const straddle = (d.callPremium||0) + (d.putPremium||0);
  // computeNetGEX/computeGammaFlip (metrics.js, IA redesign step 6) —
  // `greeks` is whatever scope the caller passed in (currently always
  // visible-range, see the callers' own comments); this function doesn't
  // decide the scope, it just computes off whatever array it's given.
  const totalGEX = computeNetGEX(greeks);
  const flipRow  = computeGammaFlip(greeks, atm);
  const thetaPct = straddle>0 ? Math.abs(d.atmTheta||0)/straddle*100 : 0;

  const alerts=[];
  if(flipRow){
    alerts.push({
      icon:'∿', clr:'var(--amber)',
      title:'GAMMA FLIP',
      text:`Regime crosses ${flipRow.netGEX>=0?'short → long':'long → short'} γ here`,
      num: fmtI(flipRow.strike)
    });
  }
  if(totalGEX<0){
    alerts.push({
      icon:'↘', clr:'var(--red)',
      title:'SHORT Γ',
      text:'Dealer hedging flows likely amplify moves',
      num: fmtN(totalGEX,3)+'B'
    });
  } else {
    // Was previously omitted whenever totalGEX>=0, which made this row
    // disappear entirely any time net dealer gamma crossed zero (even a
    // tick-to-tick flicker right around the boundary) instead of just
    // updating in place — the row should always reflect the current
    // regime, not vanish for one side of it.
    alerts.push({
      icon:'↗', clr:'var(--green)',
      title:'LONG Γ',
      text:'Dealer hedging flows likely dampen moves',
      num: fmtN(totalGEX,3)+'B'
    });
  }
  // VANNA MULTIPLIER — same formula already used for the F&O Simulator's
  // initial-paint value (see renderDashboard()'s simulator section in
  // chain-renderer.js: `1.0 + Math.abs(totalGEX) / 30`), off the same
  // totalGEX already computed above, so this row can't drift out of sync
  // with what the simulator itself shows.
  const vannaMultiplier = 1.0 + Math.abs(totalGEX) / 30;
  alerts.push({
    icon:'ν', clr:'var(--amber)',
    title:'VANNA',
    text:'GEX-implied vol-move sensitivity multiplier',
    num: fmtN(vannaMultiplier,2)+'x'
  });
  // THETA — was previously hidden entirely whenever thetaPct fell at or
  // below GREEKS_ALERT_THETA_PCT, which made the row flicker in and out
  // right around the 5% boundary instead of just updating in place (same
  // failure mode the LONG/SHORT Γ row above already had, and was fixed
  // for). Now always shown; only the color/wording changes based on
  // whether burn is currently elevated.
  const thetaElevated = thetaPct > GREEKS_ALERT_THETA_PCT;
  alerts.push({
    icon:'θ', clr: thetaElevated ? 'var(--red)' : 'var(--txt3)',
    title:'THETA',
    text: thetaElevated ? 'ATM straddle losing premium/day' : 'Theta burn within normal range',
    num: fmtN(thetaPct,1)+'%'
  });

  // Icon-badge row layout — circular colour-coded icon, title + description
  // stacked on the left, headline number on the right. Matches the
  // redesign mockup's alert-row treatment; replaces the older flat
  // .alert-row/.flag pill (single line: flag + text + num) which read as
  // a dense list rather than a set of individually scannable signals.
  // Self-contained inline styles (same approach as buildChainSummaryHtml)
  // rather than new CSS classes, since panels.css isn't in scope here.
  const rows = alerts.length
    ? alerts.map((a,i)=>`
      <div style="display:flex;align-items:center;gap:12px;padding:10px 2px;${i<alerts.length-1?'border-bottom:1px solid var(--border);':''}">
        <div style="width:38px;height:38px;flex:0 0 38px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb, ${a.clr} 16%, transparent);border:1px solid color-mix(in srgb, ${a.clr} 45%, transparent);">
          <span style="font-size:17px;color:${a.clr};line-height:1;">${a.icon}</span>
        </div>
        <div style="flex:1;min-width:0;">
          <div style="font-size:11.5px;font-weight:700;color:${a.clr};letter-spacing:.03em;">${a.title}</div>
          <div style="font-size:11px;color:var(--txt3);line-height:1.4;margin-top:2px;">${a.text}</div>
        </div>
        <div style="font-size:15px;font-weight:700;color:var(--txt);white-space:nowrap;flex:0 0 auto;">${a.num}</div>
      </div>`).join('')
    : `<div class="dd-empty">No Greek alerts right now — γ regime stable, theta normal.</div>`;

  return `<div class="section-card sc-violet" id="greeks-alerts-card" style="min-width:0;">
    <button class="section-header nav-card-header" onclick="openGreeksModal()"
       aria-label="Open Greeks &amp; GEX — view full table" title="Open full Greeks &amp; GEX table">
      <span class="section-title nav-card-header-label"><span class="section-icon">Δ</span>Greeks / Net GEX <span class="section-sub">Live, Visible Range</span></span>
      <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
    </button>
    <div style="display:flex;flex-direction:column;padding:2px 0;">
      ${rows}
    </div>
  </div>`;
};

  // Merged Greeks + Net GEX table. One <td> per strike shared by both
  // datasets (previously two separate cards each repeating the strike
  // column). The Δ/Γ/Θ/Vega tabs only swap which Greek fills the CE/PE
  // columns — the Net GEX / Regime columns are always shown alongside.
ChainView.prototype.renderGreeksGex = function(view) {
  const el=$i('grkgex-content');if(!el||!_data)return;
  const filteredChain=getFilteredChain(_data);
  // IV lookup by strike, off the CHAIN row data (ceIV/peIV) rather than
  // the greeks payload — the greeks array here only ever carried
  // cDelta/pDelta/cGamma/... /netGEX, never a per-leg IV field, which is
  // why g.cIV/g.pIV rendered as -/-. ceIV/peIV are the fields already
  // confirmed live elsewhere (strike-detail panel, chain-depth.js's IV
  // delta tracking, option-chain.js), so reuse those instead of
  // depending on the backend adding a field to a different payload.
  const chainByStrike={};
  filteredChain.forEach(r=>{chainByStrike[r.strike]=r;});
  // getVisibleRangeGreeks (metrics.js, IA redesign step 6) — same
  // visible-range filter as the Greeks/Net GEX Alerts card and Advanced
  // Analytics' Smart Money Ranking; this modal shows the identical
  // scope, just as a full per-strike table instead of a summary.
  const greeks=getVisibleRangeGreeks(_data, filteredChain);
  if(!greeks.length){el.innerHTML='<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No Greeks/GEX data.</div>';return;}
  const atm=activeAtm(_data);
  const fieldMap={
    delta:{ceKey:'cDelta',peKey:'pDelta',label:'Delta',ceClr:'var(--red)',peClr:'var(--green)',fmt:v=>fmtN(v,4)},
    gamma:{ceKey:'cGamma',peKey:'pGamma',label:'Gamma×10⁴',ceClr:'var(--amber)',peClr:'var(--amber)',fmt:v=>fmtN(v,4)},
    theta:{ceKey:'cTheta',peKey:'pTheta',label:'Theta/day',ceClr:'var(--red)',peClr:'var(--red)',fmt:v=>fmtN(v,2)},
    vega:{ceKey:'cVega',peKey:'pVega',label:'Vega/1%',ceClr:'var(--green)',peClr:'var(--green)',fmt:v=>fmtN(v,2)},
    // Capital-weighted premium locked (OI x LTP) — pulled from the CHAIN
    // row (cePremiumLocked/pePremiumLocked, wired in via
    // oi.capital_metrics.compute_capital_metrics), not the greeks array,
    // which never carries capital fields. fromChain:true tells the value
    // lookup below to read cRow instead of g.
    capital:{ceKey:'cePremiumLocked',peKey:'pePremiumLocked',label:'Premium ₹',ceClr:'var(--red)',peClr:'var(--green)',fmt:v=>'₹'+fmtK(v),fromChain:true},
  };
  const f=fieldMap[view]||fieldMap.delta;
  // capital view reads off chain rows (cRow, looked up per-strike below),
  // every other view off the greeks array — see fromChain above.
  const valSource = f.fromChain ? chainByStrike : null;
  const grkVals=greeks.map(g=>{
    const src = valSource ? (chainByStrike[g.strike]||{}) : g;
    return Math.max(Math.abs(src[f.ceKey]||0),Math.abs(src[f.peKey]||0));
  });
  const maxGrk=Math.max(...grkVals,0.0001);
  const gexVals=greeks.map(g=>Math.abs(g.netGEX||0));
  const maxGex=Math.max(...gexVals,0.0001);
  // Track fills whatever width its column actually gets (flex), instead
  // of a fixed pixel value — that's what was leaving a big empty gap
  // after Net GEX (28% column) and a smaller one after CE/PE Delta (22%
  // columns): the track never grew past 64px no matter how wide the <td>
  // actually rendered. Now every bar uses its full column, so wider
  // columns just get proportionally longer (more legible) bars instead
  // of dead space.
  function miniBar(v,max,color,fmt){
    const pct=Math.min(Math.abs(v)/max*100,100);
    const clr=color||(v>=0?'var(--green)':'var(--red)');
    return `<div style="display:flex;align-items:center;gap:6px;width:100%;min-width:0;"><div style="position:relative;flex:1 1 auto;min-width:24px;height:8px;background:rgba(255,255,255,0.06);border-radius:3px;overflow:hidden;"><div style="position:absolute;left:0;top:0;bottom:0;width:${pct.toFixed(1)}%;background:${clr};border-radius:3px;"></div></div><span style="flex-shrink:0;font-weight:600;color:${clr};font-family:var(--mono);white-space:nowrap;">${fmt(v)}</span></div>`;
  }
  // computeGammaFlip (metrics.js, IA redesign step 6)
  const flipStrike=computeGammaFlip(greeks);
  const flipStrikeVal=flipStrike?flipStrike.strike:null;
  let h=`<table class="t"><thead><tr>
    <th style="text-align:center;width:64px;">Strike</th>
    <th style="width:64px;">IV% <small>CE/PE</small></th>
    <th style="text-align:left;width:22%;">CE ${f.label}</th>
    <th style="text-align:left;width:22%;">PE ${f.label}</th>
    <th style="text-align:left;padding-left:10px;width:28%;">Net GEX</th>
    <th style="width:50px;text-align:center;">Regime</th>
  </tr></thead><tbody>`;
  greeks.forEach(g=>{
    const ia=g.strike===atm;const sc=ia?' atm-sc':'sc';
    const cRow=chainByStrike[g.strike]||{};
    const valSrc=f.fromChain?cRow:g;
    const ceV=valSrc[f.ceKey]||0;const peV=valSrc[f.peKey]||0;const gexV=g.netGEX||0;
    // Flip strike (regime transition row) gets a dashed top border as a
    // visual anchor — it now sits inline with delta/greek data too.
    const isFlip=flipStrikeVal!=null&&g.strike===flipStrikeVal;
    const rowStyle=isFlip?' style="border-top:1px dashed var(--txt3);"':'';
    h+=`<tr${rowStyle}>
      <td class="${sc}" style="white-space:nowrap;">${fmtI(g.strike)}${ia?' ★':''}</td>
      <td style="white-space:nowrap;"><span style="color:var(--red);">${fmtN(cRow.ceIV,2)}</span> / <span style="color:var(--green);">${fmtN(cRow.peIV,2)}</span></td>
      <td>${miniBar(ceV,maxGrk,f.ceClr,f.fmt)}</td>
      <td>${miniBar(peV,maxGrk,f.peClr,f.fmt)}</td>
      <td style="padding-left:10px;">${miniBar(gexV,maxGex,gexV>=0?'var(--blue)':'var(--red)',v=>fmtN(v,3)+'B')}</td>
      <td style="text-align:center;color:${gexV>=0?'var(--blue)':'var(--red)'};font-weight:600;">${gexV>=0?'Long':'Short'}</td>
    </tr>`;
  });
  h+=`</tbody></table>`;
  el.innerHTML=h;
  // computeNetGEX (metrics.js, IA redesign step 6)
  const totalGEX=computeNetGEX(greeks);
  const footEl=$i('grkgex-footer');
if(footEl){

  let institutionalView = 'Neutral';

  if(totalGEX > 10 && flipStrike && _data.spot >= flipStrike.strike){
    institutionalView = '🟢 Bullish — Dealer long gamma support';
  }
  else if(totalGEX < -10 && flipStrike && _data.spot < flipStrike.strike){
    institutionalView = '🔴 Bearish — Dealer short gamma pressure';
  }
  else if(totalGEX > 2){
    institutionalView = '🟡 Mild Bullish — Positive gamma zone';
  }
  else if(totalGEX < -2){
    institutionalView = '🟠 Mild Bearish — Negative gamma zone';
  }

  footEl.innerHTML=`
    <span>
      Total:
      <strong style="color:${sClr(totalGEX)};">
        ${fmtN(totalGEX,3)}B
      </strong>
    </span>

    <span style="color:${totalGEX>=0?'var(--blue)':'var(--red)'};">
      ${totalGEX>=0
        ? 'Dealer long γ — dampens'
        : 'Dealer short γ — amplifies'}
    </span>

    ${flipStrike
      ? `<span>
          Flip:
          <strong>${fmtI(flipStrike.strike)}</strong>
        </span>`
      : ''}

    <span>
      Institutional View:
      <strong>${institutionalView}</strong>
    </span>
  `;
 } 
};
