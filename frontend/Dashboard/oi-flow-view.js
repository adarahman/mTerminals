// ============================================================
// oi-flow-view.js
// Split out of panels-views.js. OiFlowView — OI Flow dashboard panel.
// Depends on dashboard-thresholds.js (INST_THRESHOLDS); load after it.
// ============================================================

class OiFlowView {
  constructor() {
    this.oiFlowMode = 'oi';
    this.nativeChartMode = 'combined';
    this.nativeChartView = 'butterfly';
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
  // The native modal has substantially more horizontal room than the old
  // compact card. Use it so small OI/change proportions remain legible.
  const BFLY_MAX=260;
  const COMBINED_MIN_BAR=18;
  const COMBINED_MIN_SEG=8;
  const combinedBar=(baseClass,color,width,currentOI,chgOI,anchorRight)=>{
    const w=Math.max(Math.round(width),1);
    const outer=`width:${w}px;background:${color};border:1px solid ${color};position:relative;overflow:hidden;box-sizing:border-box;`;
    if(!chgOI||!currentOI) return `<div class="${baseClass}" style="${outer}"></div>`;
    // Keep the true ratio for normal changes; reserve an 8px visual floor
    // only for tiny non-zero changes so dotted/hollow direction is still
    // recognizable. The exact value below the bar preserves precision.
    const seg=Math.min(Math.max(Math.round(Math.min(Math.abs(chgOI)/Math.abs(currentOI),1)*w),COMBINED_MIN_SEG),w);
    const side=anchorRight?'right:0;':'left:0;';
    if(chgOI>0){
      return `<div class="${baseClass} oi-combined-bar increase" style="${outer}" title="OI increase: dotted segment">
        <span class="oi-combined-segment oi-increase-segment" style="${side}width:${seg}px;"></span>
      </div>`;
    }
    return `<div class="${baseClass} oi-combined-bar decrease" style="${outer}" title="OI decrease: hollow segment">
      <span class="oi-combined-segment oi-decrease-segment" style="${side}width:${seg}px;${anchorRight?'border-left':'border-right'}:1px dashed ${color};"></span>
    </div>`;
  };
  const maxDOI=Math.max(...chain.map(r=>Math.max(Math.abs(r.ceChgOI||0),Math.abs(r.peChgOI||0))),1);
  let html='';
  chain.forEach(r=>{
    let ceV,peV,maxV,ceClr,peClr,signed;
    if(mode==='combined'){
      ceV=r.ceOI||0; peV=r.peOI||0; maxV=maxOI;
      ceClr='var(--ce)'; peClr='var(--pe)'; signed=false;
    }else if(mode==='chg'){
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
    const minBar=mode==='combined'?COMBINED_MIN_BAR:3;
    const cW=Math.max(Math.round(Math.abs(ceV)/maxV*BFLY_MAX),minBar);
    const pW=Math.max(Math.round(Math.abs(peV)/maxV*BFLY_MAX),minBar);
    const ia=r.atm||r.strike===atm;
    const sPCR=r.ceOI>0?(r.peOI||0)/r.ceOI:0;
    const pcrClr=sPCR>1?'var(--green)':sPCR<1?'var(--red)':'var(--txt3)';
    let ceLbl=(signed&&ceV>=0?'+':'')+fmtK(ceV);
    let peLbl=(signed&&peV>=0?'+':'')+fmtK(peV);
    if(mode==='combined'){
      const ceChg=r.ceChgOI||0, peChg=r.peChgOI||0;
      ceLbl += `<small class="oi-combined-change" style="color:${ceOiChgClr(ceChg)}">${ceChg>=0?'+':''}${fmtK(ceChg)}</small>`;
      peLbl += `<small class="oi-combined-change" style="color:${sClr(peChg)}">${peChg>=0?'+':''}${fmtK(peChg)}</small>`;
    }
    const ceBar=mode==='combined'
      ?combinedBar('oi-ce-bar',ceClr,cW,r.ceOI||0,r.ceChgOI||0,false)
      :`<div class="oi-ce-bar" style="width:${cW}px;background:${ceClr};"></div>`;
    const peBar=mode==='combined'
      ?combinedBar('oi-pe-bar',peClr,pW,r.peOI||0,r.peChgOI||0,true)
      :`<div class="oi-pe-bar" style="width:${pW}px;background:${peClr};"></div>`;
    html+=`<div class="oi-bfly-wrap" style="${ia?'background:rgba(18,184,134,0.06);border-radius:4px;padding:3px 4px;':''}">
      <span class="oi-bfly-fig" style="text-align:right;color:${ceClr};">${ceLbl}</span>
      <div class="oi-bfly-ce-track">${ceBar}</div>
      <span class="oi-bfly-strike" style="${ia?'color:var(--green);font-weight:600;':''}">${fmtI(r.strike)}${ia?' ★':''}</span>
      <div class="oi-bfly-pe-track">${peBar}</div>
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

  renderNativeChart(mode){
  this.nativeChartMode = mode || this.nativeChartMode || 'oi';
  const target = document.getElementById('oi-flow-native-content');
  if(!target) return;
  const d = _data || (app.data && app.data.store && app.data.store.state);
  const chain = d && typeof getFilteredChain === 'function' ? getFilteredChain(d) : [];
  if(!chain.length){
    target.innerHTML = '<div class="oic-empty">Awaiting option-chain data…</div>';
    return;
  }
  const atm = activeAtm(d);
  const maxOI = Math.max(...chain.map(r=>Math.max(Math.abs(r.ceOI||0),Math.abs(r.peOI||0))),1);
  let renderMode = this.nativeChartMode;
  let velByStrike = {}, velMax = 1;
  if(this.nativeChartMode === 'chg' && this.nativeVelocityWindow){
    const block = (d.oiVelocity||[]).find(b=>Number(b.window)===Number(this.nativeVelocityWindow));
    if(block && block.rows){
      block.rows.forEach(r=>{velByStrike[r.strike]=r;});
      velMax=Math.max(...chain.map(r=>{const v=velByStrike[r.strike]||{};return Math.max(Math.abs(v.ceDOI||0),Math.abs(v.peDOI||0));}),1);
      renderMode='vel';
    }
  }
  const modeLabel = this.nativeChartMode === 'combined' ? 'Combined OI + ΔOI'
    : renderMode === 'vel' ? `OI Change Velocity (${this.nativeVelocityWindow}m)`
    : this.nativeChartMode === 'chg' ? 'Intraday Change in OI' : 'Open Interest';
  const movers = this.buildOiTopMoversStrip(chain, velByStrike, renderMode);
  target.innerHTML = `
    <div class="oi-native-summary"><strong>${modeLabel}</strong><span>${movers}</span><span>CE <b style="color:var(--ce)">■</b> · PE <b style="color:var(--pe)">■</b> · ${this.nativeChartMode==='combined'?'Dotted = increase · Hollow = decrease · ':''}★ ATM</span></div>
    <div class="oi-native-colhead"><span>CE</span><span>CE Flow</span><span>Strike</span><span>PE Flow</span><span>PE</span><span>PCR</span></div>
    ${this.nativeChartView==='bar'
      ?'<div class="oi-native-bar-wrap"><canvas id="oi-native-bar-canvas" role="img" aria-label="CE and PE open interest flow by strike"></canvas></div>'
      :`<div class="oi-native-chart">${this.buildOiFlowRows(chain, atm, maxOI, velByStrike, velMax, renderMode)}</div>`}`;
  document.querySelectorAll('[data-oi-native-mode]').forEach(btn=>{
    const active = btn.dataset.oiNativeMode === this.nativeChartMode;
    btn.classList.toggle('active-oif', active);
    btn.setAttribute('aria-pressed', String(active));
  });
  const velocityTabs=document.getElementById('oi-native-velocity-tabs');
  if(velocityTabs) velocityTabs.hidden=this.nativeChartMode!=='chg';
  document.querySelectorAll('[data-oi-vel-window]').forEach(btn=>{
    const active=Number(btn.dataset.oiVelWindow)===Number(this.nativeVelocityWindow||0);
    btn.classList.toggle('active-oif',active);
    btn.setAttribute('aria-pressed',String(active));
  });
  document.querySelectorAll('[data-oi-native-view]').forEach(btn=>{
    const active=btn.dataset.oiNativeView===this.nativeChartView;
    btn.classList.toggle('active-oif',active);
    btn.setAttribute('aria-pressed',String(active));
  });
  if(this.nativeChartView==='bar') requestAnimationFrame(()=>this.renderNativeBarChart(chain,atm,renderMode,velByStrike,velMax));
}

  switchNativeChart(mode, el){
  this.renderNativeChart(mode);
  if(el) el.focus();
}

  setNativeVelocity(windowMin, el){
  this.nativeVelocityWindow=Number(windowMin)||null;
  this.renderNativeChart('chg');
  if(el) el.focus();
}

  switchNativeView(view, el){
  this.nativeChartView=view==='bar'?'bar':'butterfly';
  this.renderNativeChart(this.nativeChartMode);
  if(el) el.focus();
}

  renderNativeBarChart(chain,atm,mode,velByStrike,velMax){
  const canvas=document.getElementById('oi-native-bar-canvas');
  if(!canvas||!chain.length)return;
  const wrap=canvas.parentElement,W=Math.max(640,wrap.clientWidth-8),H=Math.max(360,wrap.clientHeight-8);
  const ctx=sizeCanvasIfChanged(canvas,W,H);
  const cs=getComputedStyle(document.documentElement),ce=cs.getPropertyValue('--ce').trim()||'#fa5252',pe=cs.getPropertyValue('--pe').trim()||'#12b886';
  const txt=cs.getPropertyValue('--text-tertiary').trim()||'#868e96',border=cs.getPropertyValue('--border').trim()||'#333',bg=cs.getPropertyValue('--bg-0').trim()||'#0b0e14';
  const L=52,R=18,T=26,B=48,cw=W-L-R,ch=H-T-B,zero=mode==='oi'||mode==='combined'?H-B:T+ch/2;
  const values=chain.map(r=>{const v=velByStrike[r.strike]||{};return mode==='vel'?[v.ceDOI||0,v.peDOI||0]:mode==='chg'?[r.ceChgOI||0,r.peChgOI||0]:[r.ceOI||0,r.peOI||0];});
  const max=Math.max(...values.flat().map(Math.abs),1),slot=cw/chain.length,bw=Math.max(3,Math.min(18,slot*.32));
  ctx.clearRect(0,0,W,H);ctx.strokeStyle=border;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(L,zero);ctx.lineTo(W-R,zero);ctx.stroke();
  const drawPattern=(x,y,w,h,delta,color)=>{if(!delta)return;const seg=Math.min(Math.max(Math.abs(delta)/(max||1)*Math.abs(h),8),Math.abs(h));const sy=y;if(delta<0){ctx.fillStyle=bg;ctx.fillRect(x,sy,w,seg);ctx.strokeStyle=color;ctx.setLineDash([3,2]);ctx.strokeRect(x,sy,w,seg);ctx.setLineDash([]);}else{ctx.fillStyle='rgba(255,255,255,.72)';for(let dx=x+2;dx<x+w;dx+=4)for(let dy=sy+2;dy<sy+seg;dy+=4){ctx.beginPath();ctx.arc(dx,dy,1,0,Math.PI*2);ctx.fill();}}};
  chain.forEach((r,i)=>{const x=L+i*slot+slot/2;values[i].forEach((v,j)=>{const positiveOnly=mode==='oi'||mode==='combined',h=(positiveOnly?-1:-Math.sign(v))*Math.abs(v)/max*(positiveOnly?ch:ch/2),bx=x+(j?1:-1)*(bw/2)+(j?0:-bw),by=zero+(h<0?h:0);ctx.fillStyle=j?pe:ce;ctx.fillRect(bx,by,bw,Math.max(Math.abs(h),1));if(mode==='combined')drawPattern(bx,by,bw,Math.max(Math.abs(h),1),j?(r.peChgOI||0):(r.ceChgOI||0),j?pe:ce);});if(i===0||i===chain.length-1||r.atm||r.strike===atm||i%Math.max(1,Math.ceil(chain.length/7))===0){ctx.fillStyle=r.strike===atm?'#fcc419':txt;ctx.font='9px JetBrains Mono,monospace';ctx.textAlign='center';ctx.fillText(fmtI(r.strike),x,H-B+16);}});
  ctx.fillStyle=ce;ctx.fillRect(L,T-14,9,9);ctx.fillStyle=txt;ctx.textAlign='left';ctx.fillText('CE',L+13,T-6);ctx.fillStyle=pe;ctx.fillRect(L+42,T-14,9,9);ctx.fillStyle=txt;ctx.fillText('PE',L+55,T-6);
}

  // Dashboard-native OI Flow summary. The retired standalone Butterfly
  // page used this same canonical option-chain state, so this card is now
  // the single presentation of the flow metrics.
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
    <button type="button" class="section-header nav-card-header oi-flow-chart-header" onclick="openOIDashboardModal()" aria-label="Open OI Flow chart">
      <span class="section-title nav-card-header-label"><span class="section-icon">🌊</span>OI Flow</span>
      <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
    </button>
    <div class="oic-empty">Awaiting chain data…</div>
  </div>`;
  }

  // Capital Flow belongs to D-07, not D-04. These values use the
  // backend-provided per-strike ceCapitalFlow/peCapitalFlow fields and
  // therefore remain flow (ChgOI × LTP), not premium locked (OI × LTP).
  const nullableTotal = (field) => {
    const values = chain.map((r) => r[field]).filter((v) => v != null && v !== '').map(Number).filter(Number.isFinite);
    return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
  };
  const nullablePair = (ceField, peField) => {
    const ce = nullableTotal(ceField), pe = nullableTotal(peField);
    return ce == null && pe == null ? null : (ce || 0) + (pe || 0);
  };
  const totalCeFlow = nullableTotal('ceCapitalFlow');
  const totalPeFlow = nullableTotal('peCapitalFlow');
  const netCapitalFlow = totalCeFlow == null || totalPeFlow == null ? null : totalPeFlow-totalCeFlow;
  const totalPremiumLocked = nullablePair('cePremiumLocked', 'pePremiumLocked');
  const totalPremiumTurnover = nullablePair('cePremiumTurnover', 'pePremiumTurnover');
  const totalNotionalExposure = nullablePair('ceNotionalExposure', 'peNotionalExposure');
  const capitalByStrike = chain.map((r) => ({
    strike:r.strike,
    value:(Number(r.cePremiumLocked)||0)+(Number(r.pePremiumLocked)||0)
  })).filter((r) => r.value > 0).sort((a,b) => b.value-a.value);
  const topCapital = capitalByStrike[0] || null;
  const topCapitalPct = topCapital && totalPremiumLocked > 0 ? topCapital.value / totalPremiumLocked * 100 : null;
  let topCeFlow = null, topPeFlow = null;
  chain.forEach((r) => {
    const ceFlow = Number(r.ceCapitalFlow), peFlow = Number(r.peCapitalFlow);
    if (r.ceCapitalFlow != null && Number.isFinite(ceFlow) && (!topCeFlow || Math.abs(ceFlow) > Math.abs(topCeFlow.value))) topCeFlow = {strike:r.strike, value:ceFlow};
    if (r.peCapitalFlow != null && Number.isFinite(peFlow) && (!topPeFlow || Math.abs(peFlow) > Math.abs(topPeFlow.value))) topPeFlow = {strike:r.strike, value:peFlow};
  });
  const fmtCapital = (v, signed=true) => {
    if(v==null || isNaN(v)) return '—';
    const a=Math.abs(v), sign=signed?(v>0?'+':v<0?'-':''):'';
    if(a>=1e12) return sign+'₹'+(a/1e12).toFixed(2)+' lakh Cr';
    if(a>=1e7) return sign+'₹'+(a/1e7).toFixed(2)+'Cr';
    if(a>=1e5) return sign+'₹'+(a/1e5).toFixed(2)+'L';
    if(a>=1e3) return sign+'₹'+(a/1e3).toFixed(1)+'K';
    return sign+'₹'+Math.round(a);
  };
  const capitalMetric = (label, value, qualifier) => `<div title="${value==null?'Unavailable':`Exact: ₹${Math.round(Math.abs(value)).toLocaleString('en-IN')}`}"><span>${label}</span><strong>${fmtCapital(value,false)}</strong>${qualifier?`<small>${qualifier}</small>`:''}</div>`;
  const flowAction = (v) => v==null ? 'unavailable' : v>0 ? 'premium-weighted OI added' : v<0 ? 'premium-weighted OI unwound' : 'no net day-session change';
  const netFlowRead = netCapitalFlow==null ? 'Net flow is unavailable.'
    : netCapitalFlow>0 ? 'Put-side flow leads call-side flow; a bullish positioning tilt, not proof of fresh buying.'
    : netCapitalFlow<0 ? 'Call-side flow leads put-side flow; a bearish positioning tilt, not proof of fresh writing.'
    : 'Call- and put-side day-session flow are balanced.';
  const sessionTone = netCapitalFlow==null ? 'neutral' : netCapitalFlow>0 ? 'bullish' : netCapitalFlow<0 ? 'bearish' : 'neutral';
  const sessionTitle = netCapitalFlow==null ? 'Session flow unavailable'
    : netCapitalFlow>0 ? 'Put-side flow leads · Bullish tilt'
    : netCapitalFlow<0 ? 'Call-side flow leads · Bearish tilt'
    : 'Call and put flow balanced';

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
  const fullDayNetOiChange = chain.reduce((sum, r) => sum + (Number(r.peChgOI)||0) - (Number(r.ceChgOI)||0), 0);
  const oiChangePeriods = [{label:'Full day', value:fullDayNetOiChange}, ...netOiVel.map(({windowMin,value}) => ({label:`${windowMin}m`,value}))];
  const fmtNetOiVelocity = (v) => (v==null || !Number.isFinite(v))
    ? '—'
    : `${v>0?'+':''}${fmtK(v)}`;
  const latestVelocity = netOiVel.find(({value}) => value!=null && Number.isFinite(value))?.value;
  const velocityRead = latestVelocity==null ? 'Intraday confirmation is not available yet.'
    : latestVelocity>0 ? 'Recent PE−CE velocity leans bullish and confirms put-side participation.'
    : latestVelocity<0 ? 'Recent PE−CE velocity leans bearish and confirms call-side participation.'
    : 'Recent CE and PE velocity is balanced; there is no intraday confirmation.';

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
  // The former standalone Butterfly/Bar page was removed because it
  // visualized the same canonical option-chain values. This native card
  // remains the single OI Flow summary inside the dashboard.

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
    <button type="button" class="section-header nav-card-header oi-flow-chart-header" onclick="openOIDashboardModal()" aria-label="Open OI Flow chart">
      <span class="section-title nav-card-header-label"><span class="section-icon">🌊</span>OI Snapshot · Change &amp; Capital Flow</span>
      <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
    </button>
    <div class="oi-net-velocity-section" aria-label="Net OI change by period">
      <div class="oi-flow-section-heading">
        <span class="oi-flow-step">1 · Net OI change by period</span>
        <small>Every value is PE−CE ΔOI · same visible strike range</small>
      </div>
      <div class="oi-net-velocity-strip">
        ${oiChangePeriods.map(({label,value}) => `
          <div class="oi-net-velocity-item">
            <span>${label}</span>
            <strong style="color:${value==null?'var(--text-tertiary)':signColor(value)};">${fmtNetOiVelocity(value)}</strong>
          </div>`).join('')}
      </div>
      <p class="oi-flow-velocity-read"><strong>Full day</strong> is the current-session OI change; 5m, 15m and 30m are the same OI-change measure over shorter windows. ${velocityRead}</p>
    </div>
    <div class="capital-flow-section oi-flow-frame" aria-label="Capital Flow">
      <div class="oi-flow-frame-heading">
        <div>
          <span class="oi-flow-eyebrow">Capital Flow</span>
          <h3>Where is options capital moving?</h3>
        </div>
        <span class="oi-flow-scope">Day-session ΔOI × LTP · visible range</span>
      </div>

      <section class="oi-flow-verdict ${sessionTone}" aria-label="Session flow verdict">
        <div>
          <span class="oi-flow-step">2 · Capital-weighted session verdict</span>
          <strong>${sessionTitle}</strong>
          <p>${netFlowRead}</p>
        </div>
        <div class="oi-flow-net-value">
          <span>Net PE−CE</span>
          <strong>${fmtCapital(netCapitalFlow)}</strong>
        </div>
      </section>

      <section aria-labelledby="oi-flow-compare-title">
        <div class="oi-flow-section-heading">
          <span class="oi-flow-step" id="oi-flow-compare-title">3 · Compare both sides</span>
          <small>Positive = premium-weighted OI added; negative = unwound</small>
        </div>
        <div class="oi-flow-side-grid">
          <article class="oi-flow-side-card ce">
            <div class="oi-flow-side-head"><span>CE</span><strong>Call-side flow</strong></div>
            <div class="oi-flow-side-value">${fmtCapital(totalCeFlow)}</div>
            <dl>
              <div><dt>Session state</dt><dd>${flowAction(totalCeFlow)}</dd></div>
              <div><dt>Leading strike</dt><dd>${topCeFlow ? `<button class="strike-link ce" onclick="event.stopPropagation();openOptionChainAtStrike(${topCeFlow.strike})">${fmtI(topCeFlow.strike)}</button>` : '—'}</dd></div>
            </dl>
          </article>
          <article class="oi-flow-side-card pe">
            <div class="oi-flow-side-head"><span>PE</span><strong>Put-side flow</strong></div>
            <div class="oi-flow-side-value">${fmtCapital(totalPeFlow)}</div>
            <dl>
              <div><dt>Session state</dt><dd>${flowAction(totalPeFlow)}</dd></div>
              <div><dt>Leading strike</dt><dd>${topPeFlow ? `<button class="strike-link pe" onclick="event.stopPropagation();openOptionChainAtStrike(${topPeFlow.strike})">${fmtI(topPeFlow.strike)}</button>` : '—'}</dd></div>
            </dl>
          </article>
        </div>
      </section>

      <section aria-labelledby="oi-flow-context-title">
        <div class="oi-flow-section-heading">
          <span class="oi-flow-step" id="oi-flow-context-title">4 · Capital context</span>
          <small>Scale and concentration, not directional signals</small>
        </div>
        <div class="capital-foundation-strip" aria-label="Stage 1 capital metrics">
        ${capitalMetric('Premium locked',totalPremiumLocked,'positioned premium')}
        ${capitalMetric('Premium turnover',totalPremiumTurnover,'session activity')}
        ${capitalMetric('Gross strike notional',totalNotionalExposure,'exposure scale, not cash deployed')}
        </div>
        ${topCapital ? `<div class="capital-concentration-note"><strong>Concentration:</strong> <button class="strike-link" onclick="event.stopPropagation();openOptionChainAtStrike(${topCapital.strike})">${fmtI(topCapital.strike)}</button> holds ${fmtN(topCapitalPct,1)}% of visible premium locked.</div>` : ''}
      </section>

      <details class="oi-flow-method">
        <summary>How to read these values</summary>
        <div class="capital-unit-note">Units: ₹ underlying quantity terms. OI is already lot-scaled; turnover alone converts raw volume contracts using lot size. Capital Flow is day-session ΔOI × LTP; 5m/15m/30m below is separate intraday velocity.</div>
      </details>
    </div>
  </div>`;
}
}
