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
  const totalGEX = greeks.reduce((s,g)=>s+(g.netGEX||0),0);
  const flipRow  = findGammaFlipStrike(greeks);
  const thetaPct = straddle>0 ? Math.abs(d.atmTheta||0)/straddle*100 : 0;

  const alerts=[];
  if(flipRow){
    alerts.push({
      icon:'⚡', clr:'var(--amber)',
      text:`Gamma flip at <strong>${fmtI(flipRow.strike)}</strong> — regime crosses ${flipRow.netGEX>=0?'short → long':'long → short'} γ there`
    });
  }
  if(totalGEX<0){
    alerts.push({
      icon:'⚠', clr:'var(--red)',
      text:`Dealer <strong>short gamma</strong> (${fmtN(totalGEX,3)}B) — hedging flows likely amplify moves`
    });
  }
  if(thetaPct>GREEKS_ALERT_THETA_PCT){
    alerts.push({
      icon:'⏳', clr:'var(--red)',
      text:`High theta decay — ATM straddle losing <strong>${fmtN(thetaPct,1)}%</strong> of premium/day`
    });
  }

  const rows = alerts.length
    ? alerts.map(a=>`<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:rgba(255,255,255,0.03);border-left:2px solid ${a.clr};border-radius:4px;font-size:12px;line-height:1.4;"><span style="flex-shrink:0;">${a.icon}</span><span style="color:var(--txt2);">${a.text}</span></div>`).join('')
    : `<div style="font-size:12px;color:var(--txt3);padding:6px 8px;">No Greek alerts right now — γ regime stable, theta normal.</div>`;

  return `<div class="section-card algn-card" id="greeks-alerts-card" style="min-width:0;">
    <div class="section-header">
      <span class="section-title">Greeks / Net GEX</span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="openGreeksModal()" title="Open full Greeks &amp; GEX table">Full Table →</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;padding:2px 0;">
      ${rows}
    </div>
  </div>`;
};

  // Grouped next to the alerts card above (both are "Greeks" info). Pulled
  // out into its own method — same as buildGreeksAlertsHtml — so the
  // incremental expiry-switch refresh in _rerenderChainPanels can rebuild
  // this exact card instead of duplicating the markup; previously this had
  // no id and only ever updated on a full rebuild, so it went stale
  // between expiry switches.
ChainView.prototype.buildAtmGreeksHtml = function(d) {
  return `<div class="section-card" id="atm-greeks-card">
      <div class="section-header"><span class="section-title">ATM Greeks ${fmtI(d.atm)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">Delta</span><span style="font-weight:600;font-family:var(--mono);">${fmtN(d.atmDelta,4)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">Gamma ×10⁴</span><span style="font-weight:600;font-family:var(--mono);">${fmtN(d.atmGamma,4)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">Theta / day</span><span style="color:var(--red);font-weight:600;font-family:var(--mono);">${fmtN(d.atmTheta,2)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">Vega</span><span style="font-weight:600;font-family:var(--mono);">${fmtN(d.atmVega,2)}</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">IV vs HV</span><span style="color:var(--amber);font-weight:600;">${fmtN((d.atmIV||0)-(d.hv30||0),2)}% rich</span></div>
      <div class="iv-row"><span style="color:var(--txt3);">IV rank</span><span style="color:var(--blue);font-weight:600;">${Math.round(d.ivRank||0)} / 100</span></div>
    </div>`;
};

  // Merged Greeks + Net GEX table. One <td> per strike shared by both
  // datasets (previously two separate cards each repeating the strike
  // column). The Δ/Γ/Θ/Vega tabs only swap which Greek fills the CE/PE
  // columns — the Net GEX / Regime columns are always shown alongside.
ChainView.prototype.renderGreeksGex = function(view) {
  const el=$i('grkgex-content');if(!el||!_data)return;
  const filteredChain=getFilteredChain(_data);
  const grkStrikeSet=new Set(filteredChain.map(c=>c.strike));
  // IV lookup by strike, off the CHAIN row data (ceIV/peIV) rather than
  // the greeks payload — the greeks array here only ever carried
  // cDelta/pDelta/cGamma/... /netGEX, never a per-leg IV field, which is
  // why g.cIV/g.pIV rendered as -/-. ceIV/peIV are the fields already
  // confirmed live elsewhere (strike-detail panel, chain-depth.js's IV
  // delta tracking, option-chain.js), so reuse those instead of
  // depending on the backend adding a field to a different payload.
  const chainByStrike={};
  filteredChain.forEach(r=>{chainByStrike[r.strike]=r;});
  const greeks=(_data.greeks||[]).filter(g=>grkStrikeSet.has(g.strike));
  if(!greeks.length){el.innerHTML='<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No Greeks/GEX data.</div>';return;}
  const atm=activeAtm(_data);
  const fieldMap={
    delta:{ceKey:'cDelta',peKey:'pDelta',label:'Delta',ceClr:'var(--red)',peClr:'var(--green)',fmt:v=>fmtN(v,4)},
    gamma:{ceKey:'cGamma',peKey:'pGamma',label:'Gamma×10⁴',ceClr:'var(--amber)',peClr:'var(--amber)',fmt:v=>fmtN(v,4)},
    theta:{ceKey:'cTheta',peKey:'pTheta',label:'Theta/day',ceClr:'var(--red)',peClr:'var(--red)',fmt:v=>fmtN(v,2)},
    vega:{ceKey:'cVega',peKey:'pVega',label:'Vega/1%',ceClr:'var(--green)',peClr:'var(--green)',fmt:v=>fmtN(v,2)},
  };
  const f=fieldMap[view]||fieldMap.delta;
  const grkVals=greeks.map(g=>Math.max(Math.abs(g[f.ceKey]||0),Math.abs(g[f.peKey]||0)));
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
  const flipStrike=findGammaFlipStrike(greeks);
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
    const ceV=g[f.ceKey]||0;const peV=g[f.peKey]||0;const gexV=g.netGEX||0;
    const cRow=chainByStrike[g.strike]||{};
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
  const totalGEX=greeks.reduce((s,g)=>s+(g.netGEX||0),0);
  const footEl=$i('grkgex-footer');
  if(footEl){
    footEl.innerHTML=`
      <span>Total: <strong style="color:${sClr(totalGEX)};">${fmtN(totalGEX,3)}B</strong></span>
      <span style="color:${totalGEX>=0?'var(--blue)':'var(--red)'};">${totalGEX>=0?'Dealer long γ — dampens':'Dealer short γ — amplifies'}</span>
      ${flipStrike?`<span>Flip: <strong>${fmtI(flipStrike.strike)}</strong></span>`:''}
    `;
  }
};
