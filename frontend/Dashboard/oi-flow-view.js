// ============================================================
// oi-flow-view.js
// Split out of panels-views.js. OiFlowView — OI Flow dashboard panel.
// Depends on dashboard-thresholds.js (INST_THRESHOLDS); load after it.
// ============================================================

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
  const ceHtml = ceStrike!==null ? `<span style="color:var(--txt3);">${lbl} <span style="color:var(--ce);font-weight:600;">${fmtI(ceStrike)} ▲${fmtK(ceVal)}</span></span>` : '';
  const peHtml = peStrike!==null ? `<span style="color:var(--txt3);">${lblPe} <span style="color:var(--pe);font-weight:600;">${fmtI(peStrike)} ▲${fmtK(peVal)}</span></span>` : '';
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
      ceClr='var(--ce)'; peClr='var(--pe)'; signed=false;
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
  // layout) now lives in the OI Dashboard's "Butterfly" tab (oi-flow.html
  // / oi-flow.js) so it can be viewed full-size without competing for
  // space with Greeks/GEX on the main dashboard. This card is the "glance"
  // version: biggest CE/PE build, a Block Detection readout (same scan the
  // Vol/OI Velocity by Strike panel runs), and a button straight into that
  // Butterfly tab — same pattern as the Option Chain Snapshot card that
  // replaced the old inline chain table.
  // Same find-the-biggest-strike logic buildOiTopMoversStrip() uses (kept
  // intact above, still the source for the OI Dashboard's Butterfly tab),
  // just returning the raw {strike,val} pair instead of a pre-joined
  // inline-HTML strip — buildOiFlowSummaryHtml() below renders each side
  // as its own stacked row (matching the redesign mockup) rather than a
  // single "CE | PE" line, so it needs the numbers, not pre-built markup.
  findOiBiggestBuild(chain, velByStrike, mode){
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
  const lbl=mode==='oi'?'Biggest CE OI':mode==='vel'?`Biggest CE Vel (${_velWin}m)`:'Biggest CE build';
  const lblPe=mode==='oi'?'Biggest PE OI':mode==='vel'?`Biggest PE Vel (${_velWin}m)`:'Biggest PE build';
  return { ceStrike, ceVal, peStrike, peVal, lbl, lblPe };
}

  buildOiFlowSummaryHtml(chain, atm, velByStrike, oiVelocity){
  if(!chain || !chain.length){
    return `
  <div class="oic-card" id="oi-flow-summary-card">
    <div class="oic-empty">Awaiting chain data…</div>
  </div>`;
  }

  // Capital Flow belongs to D-07, not D-04. These values use the
  // backend-provided per-strike ceCapitalFlow/peCapitalFlow fields and
  // therefore remain flow (ChgOI × LTP), not premium locked (OI × LTP).
  const totalCeFlow = chain.reduce((sum,r)=>sum+(r.ceCapitalFlow||0),0);
  const totalPeFlow = chain.reduce((sum,r)=>sum+(r.peCapitalFlow||0),0);
  const netCapitalFlow = totalPeFlow-totalCeFlow;
  let topCeFlow = null, topPeFlow = null;
  chain.forEach((r) => {
    if (!topCeFlow || Math.abs(r.ceCapitalFlow||0) > Math.abs(topCeFlow.value)) topCeFlow = {strike:r.strike, value:r.ceCapitalFlow||0};
    if (!topPeFlow || Math.abs(r.peCapitalFlow||0) > Math.abs(topPeFlow.value)) topPeFlow = {strike:r.strike, value:r.peCapitalFlow||0};
  });
  const fmtCapital = (v) => {
    if(v==null || isNaN(v)) return '—';
    const a=Math.abs(v), sign=v>0?'+':v<0?'-':'';
    if(a>=1e7) return sign+'₹'+(a/1e7).toFixed(2)+'Cr';
    if(a>=1e5) return sign+'₹'+(a/1e5).toFixed(2)+'L';
    if(a>=1e3) return sign+'₹'+(a/1e3).toFixed(1)+'K';
    return sign+'₹'+Math.round(a);
  };

  // Net OI Flow belongs to D-07. Preserve the original multi-window
  // 5m/15m/30m read without duplicating it inside D-04 or the dedicated
  // D-05 strike ledger. Net is PE velocity minus CE velocity across the
  // same currently visible strike range used by this Dashboard card.
  const visibleStrikes = new Set(chain.map(r => Number(r.strike)));
  const netOiVelocityFor = (windowMin) => {
    const block = (oiVelocity || []).find(b => Number(b.window) === windowMin);
    if(!block || !Array.isArray(block.rows)) return null;
    let ce = 0, pe = 0, hasValue = false;
    block.rows.forEach((r) => {
      if(!visibleStrikes.has(Number(r.strike))) return;
      const ceV = Number(r.ceDOI);
      const peV = Number(r.peDOI);
      if(Number.isFinite(ceV)){ ce += ceV; hasValue = true; }
      if(Number.isFinite(peV)){ pe += peV; hasValue = true; }
    });
    return hasValue ? pe - ce : null;
  };
  const netOiVel = [5,15,30].map(windowMin => ({
    windowMin,
    value: netOiVelocityFor(windowMin)
  }));
  const fmtNetOiVelocity = (v) => (v==null || !Number.isFinite(v))
    ? '—'
    : `${v>0?'+':''}${fmtK(v)}`;

  // Total PE/CE OI + PCR across the visible chain is intentionally NOT
  // recomputed here — it's the exact same aggregate the Option Chain
  // Snapshot card's "OI SUMMARY" block already shows (same getFilteredChain()
  // source), so showing it a second time here was a straight duplicate, not
  // an independent read.
  //
  // NOTE: the ATM strike's own OI/PCR tile that used to sit here was
  // removed in favor of the Block Detection readout below.
  //
  // NOTE: the "🏛️ Biggest Build" tile that used to sit here (Biggest CE
  // OI / Biggest PE OI, via findOiBiggestBuild) was removed — it's now
  // shown in the Decision Engine verdict box as "CE Wall"/"PE Wall" (same
  // computation, same style, just relabeled to match the wall terminology
  // used everywhere else on that card), so keeping a second copy here was
  // a straight duplicate. See renderDecisionBoxHtml in chain-template.js.
  //
  // NOTE (2026-08-02): the "OI Flow Snapshot" header (icon + title) and
  // its "🦋 Butterfly View →" button were removed at the user's request.
  // The Butterfly tab itself is still reachable elsewhere — the
  // "oi-flow-open-btn" in chain-template.js opens the same
  // openOIDashboardModal('butterfly') call — so nothing was orphaned,
  // this card just no longer duplicates that entry point. This card is
  // now purely the Block Detection tile, seated directly under Vol/OI
  // Velocity inside .oic-merged-card (chain-renderer.js) with no header
  // of its own.

  // Block Detection readout: #oi-flow-block-summary is a plain
  // placeholder here, not a computed value — this card doesn't scan for
  // block prints itself. simRenderVolGrid() (simulator-view.js) already
  // runs that scan every tick/scenario-slider move for the Vol/OI
  // Velocity by Strike (Block Detection) panel; it now writes its "N
  // block prints flagged • strongest STRIKE SIDE" result straight into
  // this element instead of its old home (#sdt-voi-summary), so the
  // content moved here rather than being duplicated. See the tick
  // pipeline in chain-renderer.js: this card's outerHTML is patched
  // before simInit()/simRenderVolGrid() runs in the same tick, so the
  // element below always exists by the time the real content is written
  // into it.
  //
  // Renders as a plain caption line now, not a boxed .oic-tile (own
  // background/border/icon-chip/uppercase label) — that nested-card
  // treatment made this read as its own alienated block sitting below
  // Vol/OI Velocity rather than that panel's own block-print readout,
  // even though #sdt-panel's header above already says "(Block
  // Detection)" so the tile's icon+label were pure duplication anyway.
  return `
  <div class="oic-card" id="oi-flow-summary-card">
    <div class="capital-flow-section" aria-label="Capital Flow">
      <div class="capital-flow-heading">
        <span class="capital-flow-heading-icon" aria-hidden="true">₹</span>
        <span class="capital-flow-heading-title">Capital Flow</span>
        <span class="capital-flow-heading-note">Day-session ΔOI × LTP · visible range</span>
      </div>
      <div class="capital-flow-strip" aria-label="Capital flow summary">
        <div class="capital-flow-item">
          <span class="capital-flow-label">CE Flow</span>
          ${topCeFlow ? `<button class="strike-link ce" onclick="event.stopPropagation();openOptionChainAtStrike(${topCeFlow.strike})">${fmtI(topCeFlow.strike)}</button>` : ''}
          <strong style="color:var(--ce);">${fmtCapital(totalCeFlow)}</strong>
        </div>
        <div class="capital-flow-item">
          <span class="capital-flow-label">PE Flow</span>
          ${topPeFlow ? `<button class="strike-link pe" onclick="event.stopPropagation();openOptionChainAtStrike(${topPeFlow.strike})">${fmtI(topPeFlow.strike)}</button>` : ''}
          <strong style="color:var(--pe);">${fmtCapital(totalPeFlow)}</strong>
        </div>
        <div class="capital-flow-item capital-flow-net">
          <span class="capital-flow-label">Net PE−CE</span>
          <strong style="color:${signColor(netCapitalFlow)};">${fmtCapital(netCapitalFlow)}</strong>
        </div>
      </div>
    </div>
    <div class="oi-net-velocity-section" aria-label="Net OI Flow by velocity window">
      <div class="oi-net-velocity-heading">
        <span class="oi-net-velocity-title">Net OI Flow</span>
        <span class="oi-net-velocity-note">PE−CE ΔOI velocity · visible range</span>
      </div>
      <div class="oi-net-velocity-strip">
        ${netOiVel.map(({windowMin,value}) => `
          <div class="oi-net-velocity-item">
            <span>${windowMin}m</span>
            <strong style="color:${value==null?'var(--text-tertiary)':signColor(value)};">${fmtNetOiVelocity(value)}</strong>
          </div>`).join('')}
      </div>
    </div>
    <div class="oi-flow-block-line" id="oi-flow-block-summary">Loading…</div>
  </div>`;
}
}

