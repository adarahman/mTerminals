// Option-chain velocity, IV surface and secondary analytics rendering.

const _chainLedgerScrollState = { top: 0, left: 0, activeUntil: 0 };

function _bindChainLedgerScrollGuard(card) {
  const scroll = card && card.querySelector('.oc-native-scroll');
  if(!scroll || scroll.dataset.scrollGuardBound === '1') return scroll;
  scroll.dataset.scrollGuardBound = '1';
  scroll.addEventListener('scroll', () => {
    _chainLedgerScrollState.top = scroll.scrollTop;
    _chainLedgerScrollState.left = scroll.scrollLeft;
    // Trackpad momentum produces several events after the fingers lift.
    // Keep the live-tick renderer away until that gesture has settled.
    _chainLedgerScrollState.activeUntil = Date.now() + 1200;
  }, { passive: true });
  return scroll;
}

function _patchExpandedLedgerRows(currentTable, freshTable) {
  if(!currentTable || !freshTable) return;
  const currentRows = Array.from(currentTable.querySelectorAll('tbody tr'));
  const freshRows = Array.from(freshTable.querySelectorAll('tbody tr'));
  if(currentRows.length !== freshRows.length) return;
  currentRows.forEach((row, rowIndex) => {
    const freshRow = freshRows[rowIndex];
    // Preserve each physical row/cell so the modal's scroll target remains
    // stable; update only the live contents and presentation attributes.
    row.className = freshRow.className;
    row.hidden = freshRow.hidden;
    Array.from(row.cells).forEach((cell, cellIndex) => {
      const freshCell = freshRow.cells[cellIndex];
      if(!freshCell) return;
      if(cell.innerHTML !== freshCell.innerHTML) cell.innerHTML = freshCell.innerHTML;
      cell.className = freshCell.className;
      const style = freshCell.getAttribute('style');
      if(style == null) cell.removeAttribute('style'); else cell.setAttribute('style', style);
      const title = freshCell.getAttribute('title');
      if(title == null) cell.removeAttribute('title'); else cell.setAttribute('title', title);
    });
  });
}

ChainView.prototype.renderVelocity = function(win) {
  const el=$i('vel-content');if(!el||!_data)return;
  const vel=_data.oiVelocity;
  if(!vel||!vel.length){el.innerHTML='<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No OI velocity data.</div>';return;}
  const block=vel.find(b=>b.window===win)||vel[0];
  const chainStrikes=new Set(getFilteredChain(_data).map(c=>c.strike));
  const rows=(block.rows||[]).filter(r=>chainStrikes.size===0||chainStrikes.has(r.strike));
  if(!rows.length){el.innerHTML=`<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No data for ${win}-min window.</div>`;return;}
  const maxAbs=Math.max(...rows.map(r=>Math.max(Math.abs(r.ceDOI||0),Math.abs(r.peDOI||0))),1);
  const atm=activeAtm(_data);
  let h=`<table class="t"><thead><tr>
    <th style="text-align:center;width:62px;">Strike</th>
    <th style="width:56px;">CE now</th><th style="width:90px;">CE ΔOI</th><th style="width:44px;">CE LTP</th>
    <th style="width:56px;">PE now</th><th style="width:90px;">PE ΔOI</th><th style="width:44px;">PE LTP</th>
    <th style="text-align:left;width:96px;">Signal</th>
  </tr></thead><tbody>`;
  rows.forEach(r=>{
    const ia=r.strike===atm;const sc=ia?' atm-sc':'sc';
    function velDOICell(v,maxAbs){
      const pct=maxAbs>0?Math.min(Math.abs(v)/maxAbs*24,24):0;
      const bar=v>=0?`<div style="width:${pct.toFixed(0)}px;background:var(--green);border-radius:2px;height:8px;display:inline-block;flex-shrink:1;max-width:24px;"></div>`:`<div style="width:${pct.toFixed(0)}px;background:var(--red);border-radius:2px;height:8px;display:inline-block;flex-shrink:1;max-width:24px;"></div>`;
      return `<div style="display:flex;align-items:center;gap:4px;justify-content:flex-end;overflow:hidden;min-width:0;">${bar}<span style="color:${sClr(v)};font-size:10px;font-family:var(--mono);white-space:nowrap;flex-shrink:0;">${v>=0?'+':''}${fmtK(v)}</span></div>`;
    }
    h+=`<tr>
      <td class="${sc}">${fmtI(r.strike)}${ia?' ★':''}</td>
      <td style="font-size:10px;color:var(--txt2);">${fmtK(r.ceNow)}</td>
      <td>${velDOICell(r.ceDOI,maxAbs)}</td>
      <td style="font-weight:600;font-family:var(--mono);">${fmtN(r.ceLTP,1)}</td>
      <td style="font-size:10px;color:var(--txt2);">${fmtK(r.peNow)}</td>
      <td>${velDOICell(r.peDOI,maxAbs)}</td>
      <td style="font-weight:600;font-family:var(--mono);">${fmtN(r.peLTP,1)}</td>
      <td style="text-align:left;"><span class="sp sp-n">${r.signal||'—'}</span></td>
    </tr>`;
  });
  const netCE=rows.reduce((s,r)=>s+(r.ceDOI||0),0);
  const netPE=rows.reduce((s,r)=>s+(r.peDOI||0),0);
  h+=`</tbody></table>
    <div class="section-footer">
      <span>CE builds: <strong style="color:var(--red);">${rows.filter(r=>r.ceDOI>0).length}/${rows.length}</strong></span>
      <span>PE builds: <strong style="color:var(--green);">${rows.filter(r=>r.peDOI>0).length}/${rows.length}</strong></span>
      <span>Net CE ΔOI: <strong style="color:${sClr(netCE)}">${netCE>=0?'+':''}${fmtK(netCE)}</strong></span>
      <span>Net PE ΔOI: <strong style="color:${sClr(netPE)}">${netPE>=0?'+':''}${fmtK(netPE)}</strong></span>
      <span>Window: <strong>${win} min</strong></span>
    </div>`;
  el.innerHTML=h;
};

  // Writes the full IV surface (buildIvSurfaceHtml above) into the modal's
  // static content div. Reads _data itself (same pattern as
  // renderGreeksGex(view) below) so it can be called with no args from
  // renderDashboard's post-render block, live ticks, and expiry switches.
ChainView.prototype.renderIvSurfaceModal = function() {
  const el = $i('iv-surface-content');
  if(!el || !_data) return;
  const currentRows = el.querySelector('.iv-surface-rows');
  const scrollTop = currentRows ? currentRows.scrollTop : 0;
  const scrollLeft = currentRows ? currentRows.scrollLeft : 0;
  const chain = getFilteredChain(_data);
  const atm = activeAtm(_data);
  el.innerHTML = this.buildIvSurfaceHtml(_data, chain, atm);
  const refreshedRows = el.querySelector('.iv-surface-rows');
  if(refreshedRows){
    refreshedRows.scrollTop = scrollTop;
    refreshedRows.scrollLeft = scrollLeft;
  }
};

  ChainView.prototype.onExpiryChange = function(selectedExpiry) {
  if(!_data || !selectedExpiry) {
    return;
  }
  const activeExpiry = _data.expiry || '';
  const _same = activeExpiry && (
    selectedExpiry === activeExpiry
    || (typeof parseExpiryDate === "function" && parseExpiryDate(selectedExpiry) === parseExpiryDate(activeExpiry))
  );
  if(_same) return; // already showing this expiry, nothing to do

  const sel = (typeof getExpirySelectNode === 'function') ? getExpirySelectNode() : null;
  if (sel) {
    sel.value = selectedExpiry;
    sel.dataset.pendingExpiry = selectedExpiry;
    sel.dataset.pendingExpiryTime = Date.now().toString();
  }

  const base = (_wsUrl || '').split('?')[0];
  const params = new URLSearchParams((_wsUrl || '').split('?')[1] || '');
  params.set('expiry', selectedExpiry);
  const newUrl = `${base}?${params.toString()}`;
  if (window.eventBus) window.eventBus.emit('expiry:change', { expiry: selectedExpiry });
  connectWebSocket(newUrl);
};

ChainView.prototype._rerenderChainPanels = function() {
  if(!_data) return;
  if(app.modal && typeof app.modal._updateOptionChainContext === 'function') app.modal._updateOptionChainContext();

  const chain          = getFilteredChain(_data);
  const chainStrikeSet = new Set(chain.map(r=>r.strike)); // still needed below (vol/OI velocity totals)
  const atm            = activeAtm(_data);
  const greeksAll      = _data.greeks || [];
  // getVisibleRangeGreeks (metrics.js, IA redesign step 6) — same
  // visible-range filter as renderDashboard's `greeks`.
  const greeks         = getVisibleRangeGreeks(_data, chain);
  const velBlock       = (_data.oiVelocity||[]).find(b=>b.window===_velWin)||(_data.oiVelocity||[])[0];
  const velByStrike    = {};
  if(velBlock&&velBlock.rows) velBlock.rows.forEach(vr=>{velByStrike[vr.strike]=vr;});
  const velMax         = Math.max(...chain.map(r=>{const vr=velByStrike[r.strike]||{};return Math.max(Math.abs(vr.ceDOI||0),Math.abs(vr.peDOI||0));}),1);
  const oiAnnot        = (_data.decision&&_data.decision.oiAnnotations)||{};
  const maxOI          = Math.max(...chain.map(r=>Math.max(r.ceOI||0,r.peOI||0)),1);

  // ── 1. Chain table body ───────────────────────────────────────────────────
  const chainEl = document.getElementById('chain-body');
  if(chainEl){
    const chainWrap = $i('chain-scroll');
    const previousChainScrollTop = chainWrap ? chainWrap.scrollTop : null;
    const chainViewportKey = `${_data.symbol || ''}|${_data.expiry || ''}|${typeof _chainRange !== 'undefined' ? _chainRange : ''}`;
    const chainViewportChanged = this._lastIncrementalChainViewportKey !== undefined
      && this._lastIncrementalChainViewportKey !== chainViewportKey;
    this._lastIncrementalChainViewportKey = chainViewportKey;
    let rows='';
    chain.forEach(r=>{
      const ia=r.atm||r.strike===atm; const ac=ia?' atm':''; const acs=ia?' atm-sc':'sc';
      const g=greeks.find(x=>x.strike===r.strike)||{};
      const sk=r.strike;
      const vr=velByStrike[sk]||{};
      const ceVelDOI=vr.ceDOI!=null?vr.ceDOI:null;
      const peVelDOI=vr.peDOI!=null?vr.peDOI:null;
      const cs=combinedSignal(r.ceSignal,r.peSignal);
      const annot=oiAnnot[String(sk)]||{};
      const rowTitle=annot.ce||annot.pe?`CE: ${annot.ce||'—'} | PE: ${annot.pe||'—'}`:'Click to show/hide Greeks';
      rows+=`<tr${ia?' id="chain-row-atm"':''} style="cursor:pointer;" tabindex="0" aria-label="Strike ${sk}; press Enter for Greeks" onclick="toggleGreekRow(${sk})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleGreekRow(${sk})}" title="${rowTitle}">`;
      rows+=`<td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.ceVol)}</td>
        <td class="${ac}">${velMiniCell(ceVelDOI,velMax,ceOiChgClr(ceVelDOI))}</td>
        <td class="${ac} pt-ltp-click" role="button" tabindex="0" aria-label="Trade ${sk} CE" style="font-weight:600;font-family:var(--mono);" onclick="event.stopPropagation();ptOpenQuickOrder(event,${sk},'CE',${r.ceLTP!=null?r.ceLTP:'null'})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();ptOpenQuickOrder(event,${sk},'CE',${r.ceLTP!=null?r.ceLTP:'null'})}" title="Click to trade this strike">${fmtN(r.ceLTP,1)}</td>
        <td class="${ac}" style="color:${ceOiChgClr(r.ceDOI)};font-size:10px;">${(r.ceDOI||0)>=0?'+':''}${fmtK(r.ceDOI)}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.ceOI)}</td>
        <td class="${acs}" style="white-space:nowrap;line-height:1.15;">${fmtI(r.strike)}${ia?' ★':''}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.peOI)}</td>
        <td class="${ac}" style="color:${sClr(r.peDOI)};font-size:10px;">${(r.peDOI||0)>=0?'+':''}${fmtK(r.peDOI)}</td>
        <td class="${ac} pt-ltp-click" role="button" tabindex="0" aria-label="Trade ${sk} PE" style="font-weight:600;font-family:var(--mono);" onclick="event.stopPropagation();ptOpenQuickOrder(event,${sk},'PE',${r.peLTP!=null?r.peLTP:'null'})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();ptOpenQuickOrder(event,${sk},'PE',${r.peLTP!=null?r.peLTP:'null'})}" title="Click to trade this strike">${fmtN(r.peLTP,1)}</td>
        <td class="${ac}">${velMiniCell(peVelDOI,velMax,sClr(peVelDOI))}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.peVol)}</td>
        <td style="text-align:right;padding-right:10px;"><span class="sp ${cs.cls}">${cs.label}</span></td>
      </tr>
      ${g.cDelta!=null?`<tr id="grk-row-${sk}" class="grk-row" style="display:none;">
        <td colspan="12" style="text-align:left;padding:4px 12px;white-space:nowrap;font-size:10px;color:var(--txt3);">
          <span style="display:inline-block;min-width:140px;">CΔ <strong style="color:var(--blue);">${fmtN(g.cDelta,3)}</strong> &nbsp;Γ×10⁴ <strong style="color:var(--amber);">${fmtN(g.cGamma,3)}</strong> &nbsp;Θ <strong style="color:var(--red);">${fmtN(g.cTheta,2)}</strong> &nbsp;Vega <strong style="color:var(--blue);">${fmtN(g.cVega,2)}</strong></span>
          <span style="display:inline-block;min-width:80px;margin-left:8px;">GEX <strong style="color:${sClr(g.netGEX||0)};">${fmtN(g.netGEX,3)}B</strong></span>
          <span style="display:inline-block;min-width:110px;margin-left:30px;">CE IV <strong style="color:var(--red);">${fmtN(r.ceIV,2)}%</strong> &nbsp;PE IV <strong style="color:var(--green);">${fmtN(r.peIV,2)}%</strong></span>
          <span style="display:inline-block;min-width:80px;margin-left:20px;">PΔ <strong style="color:var(--red);">${fmtN(g.pDelta,3)}</strong></span>
          <span style="display:inline-block;min-width:160px;margin-left:20px;">CE Signal <strong class="sp ${spClass(r.ceSignal)}">${r.ceSignal||'—'}</strong></span>
          <span style="display:inline-block;min-width:160px;margin-left:10px;">PE Signal <strong class="sp ${spClass(r.peSignal)}">${r.peSignal||'—'}</strong></span>
        </td>
      </tr>`:''}`;
    });
    chainEl.innerHTML = rows;
    if(_greeksVisible) document.querySelectorAll('[id^="grk-row-"]').forEach(el=>{el.style.display='';});
    // Live price/OI updates rebuild the tbody, but must not pull someone
    // browsing distant strikes back to ATM. Recenter only when the actual
    // symbol/expiry/range viewport changes; otherwise restore the exact
    // scroll offset captured before this tick's DOM update.
    if(chainViewportChanged) _centerChainOnATM=true;
    requestAnimationFrame(()=>app.chain.sizeAndScrollChain(previousChainScrollTop));
  }

  // ── 2. DTE pill ──────────────────────────────────────────────────────────
  const dteEl = document.getElementById('dte-display');
  if(dteEl){
    const dte = _data.dte || 0;
    dteEl.textContent = '· '+dte+'d';
    dteEl.style.color = dte<=1?'var(--red)':dte<=3?'var(--amber)':'var(--amber)';
  }

  // ── 3. Right analytics panel ──────────────────────────────────────────────
  const rpEl = document.querySelector('.chain-right-panel');
  if(rpEl){
    const totCeOI  = chain.reduce((s,r)=>s+(r.ceOI||0),0);
    const totPeOI  = chain.reduce((s,r)=>s+(r.peOI||0),0);
    const totCeDOI = chain.reduce((s,r)=>s+(r.ceChgOI||0),0);
    const totPeDOI = chain.reduce((s,r)=>s+(r.peChgOI||0),0);
    const velBlockRP=((_data.oiVelocity||[]).find(b=>b.window===_velWin)||(_data.oiVelocity||[])[0]);
    const totCeVel=(velBlockRP&&velBlockRP.rows||[]).filter(r=>chainStrikeSet.has(r.strike)).reduce((s,r)=>s+(r.ceDOI||0),0);
    const totPeVel=(velBlockRP&&velBlockRP.rows||[]).filter(r=>chainStrikeSet.has(r.strike)).reduce((s,r)=>s+(r.peDOI||0),0);
    const maxDOIrp=Math.max(Math.abs(totCeDOI),Math.abs(totPeDOI),1);
    const maxVelrp=Math.max(Math.abs(totCeVel),Math.abs(totPeVel),1);
    function rpBar(v,max,clr){const w=Math.max(Math.round(Math.abs(v)/max*72),2);return `<div class="crp-spark-wrap"><div class="crp-spark" style="width:${w}px;background:${clr};"></div><span style="font-size:9px;font-family:var(--mono);color:${clr};">${fmtK(v)}</span></div>`;}
    const bullStrikes=chain.filter(r=>{const cs=combinedSignal(r.ceSignal,r.peSignal);return cs.cls==='sp-strongbull'||cs.cls==='sp-bull';}).length;
    const bearStrikes=chain.filter(r=>{const cs=combinedSignal(r.ceSignal,r.peSignal);return cs.cls==='sp-strongbear'||cs.cls==='sp-bear';}).length;
    const aggBias=bullStrikes>bearStrikes?{label:'Bullish',cls:'sp-bull'}:bearStrikes>bullStrikes?{label:'Bearish',cls:'sp-bear'}:{label:'Mixed',cls:'sp-mixed'};
    const panelPCR=totCeOI>0?(totPeOI/totCeOI).toFixed(2):'—';
    const pcrColor=parseFloat(panelPCR)>1?'var(--green)':parseFloat(panelPCR)<0.8?'var(--red)':'var(--amber)';
    const netOI=totPeOI-totCeOI; const netDOI=totPeDOI-totCeDOI; const netVel=totPeVel-totCeVel;
    const netAbsMax=Math.max(Math.abs(netOI),Math.abs(netDOI),Math.abs(netVel),1);
    const arpBarW=(v,max)=>Math.max(Math.round(Math.abs(v)/max*72),3);
    const totCeVolChg=chain.reduce((s,r)=>s+(r.ceVolChg||0),0);
    const totPeVolChg=chain.reduce((s,r)=>s+(r.peVolChg||0),0);
    const maxVolChg=Math.max(Math.abs(totCeVolChg),Math.abs(totPeVolChg),1);
    // ── VOL VEL FIX (v2) ──
    // v1 diffed against the *previous render*, which fires almost every WS
    // tick (multiple times a second) — not a "(${_velWin}m)" velocity at
    // all, just a sub-second delta. That's why it was spiking from near-zero
    // to large and back on every tick instead of showing a stable 5-minute
    // trend. Fix: keep a timestamped history buffer and diff each strike's
    // ceVol/peVol against the snapshot closest to _velWin minutes ago —
    // same windowing concept the backend's oiVelocity block already applies
    // to OI Vel, just computed client-side since no equivalent volume field
    // exists in that payload.
    this._volHistory = this._volHistory || [];
    const _now = Date.now();
    const _nowSnap = {};
    chain.forEach(r=>{ _nowSnap[r.strike] = { ceVol: r.ceVol, peVol: r.peVol }; });
    this._volHistory.push({ ts: _now, snap: _nowSnap });
    const _windowMs = _velWin * 60 * 1000;
    const _cutoff = _now - _windowMs;
    // Trim history once entries are more than one window past the cutoff —
    // keeps memory bounded without discarding the reference sample we need.
    while (this._volHistory.length > 1 && this._volHistory[1].ts < _cutoff - _windowMs) this._volHistory.shift();
    // Pick the newest sample that's still at least a full window old. Until
    // enough history has accumulated (e.g. just after page load), this
    // falls back to the oldest sample available — the window is shorter
    // than _velWin for the first few minutes, then self-corrects.
    let _refSnap = this._volHistory[0].snap;
    for (const h of this._volHistory) { if (h.ts <= _cutoff) _refSnap = h.snap; else break; }
    let totCeVelVol = 0, totPeVelVol = 0;
    chain.forEach(r=>{
      const prev = _refSnap[r.strike];
      if (prev) {
        if (r.ceVol != null && prev.ceVol != null) totCeVelVol += (r.ceVol - prev.ceVol);
        if (r.peVol != null && prev.peVol != null) totPeVelVol += (r.peVol - prev.peVol);
      }
    });
    const maxVelVol=Math.max(Math.abs(totCeVelVol),Math.abs(totPeVelVol),1);
    rpEl.innerHTML=`
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:8px;">
      <div class="arp-row" style="padding-bottom:5px;margin-bottom:4px;border-bottom:1px solid var(--border);">
        <span class="arp-key">Signal</span>
        <div class="arp-val"><span class="sp ${aggBias.cls}" style="font-size:10px;font-weight:700;">${aggBias.label}</span><span style="font-size:9px;color:var(--txt3);margin-left:4px;">${bullStrikes}↑ ${bearStrikes}↓</span></div>
      </div>
      <div class="arp-row"><span class="arp-key">Net OI</span><div class="arp-val"><span class="arp-num" style="color:${signColor(netOI)};">${netOI>=0?'+':''}${fmtK(netOI)}</span><div class="arp-bar" style="width:${arpBarW(netOI,netAbsMax)}px;background:${signColor(netOI)};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Chg OI</span><div class="arp-val"><span class="arp-num" style="color:${signColor(netDOI)};">${netDOI>=0?'+':''}${fmtK(netDOI)}</span><div class="arp-bar" style="width:${arpBarW(netDOI,netAbsMax)}px;background:${signColor(netDOI)};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Vel OI</span><div class="arp-val"><span class="arp-num" style="color:${signColor(netVel)};">${netVel==null||isNaN(netVel)?'—':(netVel>=0?'+':'')+fmtK(netVel)}</span><div class="arp-bar" style="width:${arpBarW(netVel,netAbsMax)}px;background:${signColor(netVel)};"></div></div></div>
      <div style="padding-top:5px;margin-top:4px;border-top:1px solid var(--border);">
        <div style="margin-bottom:6px;">
          <div style="display:flex;justify-content:space-between;font-size:8px;font-family:var(--mono);margin-bottom:2px;">
            <span style="color:var(--red);">CE ${totCeOI>0?Math.round(totCeOI/(totCeOI+totPeOI)*100):50}%</span>
            <span style="color:var(--txt3);font-size:7px;text-transform:uppercase;letter-spacing:.05em;">OI Split</span>
            <span style="color:var(--green);">PE ${totPeOI>0?Math.round(totPeOI/(totCeOI+totPeOI)*100):50}%</span>
          </div>
          <div class="oi-flow-bar">
            <div class="oi-flow-ce" style="flex:${totCeOI>0?Math.round(totCeOI/(totCeOI+totPeOI)*100):50};"></div>
            <div class="oi-flow-pe" style="flex:${totPeOI>0?Math.round(totPeOI/(totCeOI+totPeOI)*100):50};"></div>
          </div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;">
        <span class="arp-key">PCR <span style="font-size:8px;font-weight:400;text-transform:none;">(visible)</span></span>
        <span style="font-size:14px;font-weight:700;font-family:var(--mono);color:${pcrColor};">${panelPCR}</span>
        </div>
      </div>
    </div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:8px;">
      <div class="crp-title" style="margin-bottom:6px;">OI Analytics <span style="color:var(--txt3);font-weight:400;">(${_velWin}m)</span></div>
      <div style="display:grid;grid-template-columns:64px 1fr 1fr;gap:2px;margin-bottom:4px;">
        <div></div><div class="crp-head-ce">CE</div><div class="crp-head-pe">PE</div>
      </div>
      <div class="crp-row"><span class="crp-label">OI</span><div class="crp-ce">${fmtK(totCeOI)}</div><div class="crp-pe">${fmtK(totPeOI)}</div></div>
      <div class="crp-row"><span class="crp-label">Chg OI</span>${rpBar(totCeDOI,maxDOIrp,totCeDOI>=0?'var(--red)':'var(--green)')}${rpBar(totPeDOI,maxDOIrp,totPeDOI>=0?'var(--green)':'var(--red)')}</div>
      <div class="crp-row"><span class="crp-label">OI Vel</span>${rpBar(totCeVel,maxVelrp,totCeVel>=0?'var(--red)':'var(--green)')}${rpBar(totPeVel,maxVelrp,totPeVel>=0?'var(--green)':'var(--red)')}</div>
    </div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;">
      <div class="crp-title" style="margin-bottom:6px;">Volume Analytics <span style="color:var(--txt3);font-weight:400;">(${_velWin}m)</span></div>
      <div style="display:grid;grid-template-columns:64px 1fr 1fr;gap:2px;margin-bottom:4px;">
        <div></div><div class="crp-head-ce">CE</div><div class="crp-head-pe">PE</div>
      </div>
      <div class="crp-row"><span class="crp-label">Vol</span><div class="crp-ce">${fmtK(chain.reduce((s,r)=>s+(r.ceVol||0),0))}</div><div class="crp-pe">${fmtK(chain.reduce((s,r)=>s+(r.peVol||0),0))}</div></div>
      <div class="crp-row"><span class="crp-label">Vol Chg</span>${rpBar(totCeVolChg,maxVolChg,totCeVolChg>=0?'var(--red)':'var(--green)')}${rpBar(totPeVolChg,maxVolChg,totPeVolChg>=0?'var(--green)':'var(--red)')}</div>
      <div class="crp-row"><span class="crp-label">Vol Vel</span>${rpBar(totCeVelVol,maxVelVol,totCeVelVol>=0?'var(--red)':'var(--green)')}${rpBar(totPeVelVol,maxVelVol,totPeVelVol>=0?'var(--green)':'var(--red)')}</div>
    </div>`;
  }

  // NOTE: Conviction Multiplier Gauge no longer has its own tick-refresh
  // block here — it moved inside advanced-analytics-card (see
  // buildAdvancedAnalyticsHtml), which already re-derives and
  // outerHTML-diffs its full contents, including this card, every tick.

  // 3b. Option Chain Snapshot card (main-dashboard OI/Chg OI/dOI/Volume
  // summary). This was previously only ever built once, inside the full
  // renderDashboard() rebuild (buildChainSummaryHtml(d) at the top of this
  // file) — never on a WS tick or expiry switch — so it went stale and
  // silently drifted from the OI Flow Snapshot card below (which *does*
  // refresh every tick), even though both read the exact same
  // getFilteredChain(_data) source. Same fix as oi-flow-summary-card /
  // greeks-alerts-card / atm-greeks-card just below: outerHTML-diff it in
  // here too, so all four summary cards stay in lockstep tick-to-tick.
  // FIX: this card's header (now a .nav-card-header link, previously the
  // "Full Chain" button — see components.css) was getting torn out from
  // under an in-progress click by the outerHTML swap below — same freeze
  // bug the Decision Detail guard exists for. guardKey skips the
  // destructive rebuild while a click on this card is mid-gesture;
  // dataset.lastHtml stays stale so the very next tick retries once the
  // click has committed, same as refreshDecisionBoxGuarded.
  const chainSummaryCard = document.getElementById('chain-summary-card');
  const expandedChain = document.getElementById('option-chain-table');
  if(chainSummaryCard && expandedChain && !expandedChain.hidden){
    // Never replace the physical scroll container while it is expanded.
    // A wheel/trackpad gesture targets that exact DOM node; outerHTML
    // replacement detaches it mid-gesture and makes scrolling appear
    // completely broken even when scrollTop is restored afterward.
    const freshHtml = app.chain.buildChainSummaryHtml(_data);
    const template = document.createElement('template');
    template.innerHTML = freshHtml.trim();
    const freshCard = template.content.firstElementChild;
    const copyInner = (selector) => {
      const current = chainSummaryCard.querySelector(selector);
      const fresh = freshCard && freshCard.querySelector(selector);
      if(current && fresh) current.innerHTML = fresh.innerHTML;
    };
    // Keep the summary and expanded ledger current. During trackpad/wheel
    // momentum, leave every physical row and its contents untouched; once
    // the gesture settles, patch values inside the already-mounted rows.
    // This preserves scrolling without freezing market ticks until close.
    copyInner('.oi-snap-overview');
    copyInner('.oi-snap-grid');
    _bindChainLedgerScrollGuard(expandedChain);
    if(Date.now() >= _chainLedgerScrollState.activeUntil){
      _patchExpandedLedgerRows(
        expandedChain,
        freshCard && freshCard.querySelector('#option-chain-table'),
      );
    }
    const currentBadge = chainSummaryCard.querySelector('.oi-snap-badge');
    const freshBadge = freshCard && freshCard.querySelector('.oi-snap-badge');
    if(currentBadge && freshBadge) currentBadge.textContent = freshBadge.textContent;
    chainSummaryCard.dataset.lastHtml = freshHtml;
  } else patchOuterHtmlIfChanged('chain-summary-card', () => app.chain.buildChainSummaryHtml(_data), {
    guardKey: 'chainSummary',
    bindGuard: true,
    shouldSkip: (card) => {
      _bindChainLedgerScrollGuard(card);
      // A native select is dismissed when its DOM node is replaced. Live
      // ticks can arrive several times while the range menu is open, so
      // keep the current card mounted until the user finishes selecting.
      // The next tick after blur applies any values that changed meanwhile.
      const rangeSelectOpen = document.activeElement
        && document.activeElement.matches('[data-chain-range-select]')
        && card.contains(document.activeElement);
      return rangeSelectOpen || Date.now() < _chainLedgerScrollState.activeUntil;
    },
    preserveState: (card) => {
      const scroll = _bindChainLedgerScrollGuard(card);
      if(scroll){
        _chainLedgerScrollState.top = scroll.scrollTop;
        _chainLedgerScrollState.left = scroll.scrollLeft;
      }
      return { top: _chainLedgerScrollState.top, left: _chainLedgerScrollState.left };
    },
    restoreState: (card, position) => {
      if(!position) return;
      const scroll = _bindChainLedgerScrollGuard(card);
      if(scroll){
        const restore = () => {
          scroll.scrollTop = position.top;
          scroll.scrollLeft = position.left;
        };
        restore();
        // outerHTML replacement can finish layout after the synchronous
        // assignment; repeat once on the next frame to make restoration
        // deterministic across browsers.
        requestAnimationFrame(restore);
      }
    }
  });

  // 4. OI Flow Snapshot card (compact — full butterfly table now lives in
  // the OI Dashboard's Butterfly tab, see buildOiFlowSummaryHtml()).
  // buildOiFlowSummaryHtml() returns the whole card including its own
  // #oi-flow-summary-card wrapper — outerHTML (not innerHTML) so the
  // dataset-diff cache stays meaningful (it lives on the element itself,
  // which outerHTML replaces wholesale).
  patchOuterHtmlIfChanged('oi-flow-summary-card', () => buildOiFlowSummaryHtml(chain, atm, velByStrike, _data.oiVelocity, _data));

  // 4b. Greeks Alerts card (gamma flip / short-gamma regime / theta decay)
  // — now lives in the row2 Tier-2 row alongside Chain Summary and
  // FII/DII, same outerHTML-diff treatment as the OI Flow card above, so
  // an expiry switch reflects the new expiry's Greeks immediately instead
  // of waiting for the next tick.
  patchOuterHtmlIfChanged('greeks-alerts-card', () => app.chain.buildGreeksAlertsHtml(greeks, atm, _data));

  // 4b-ii. FII/DII Sentiment card — now rendered inside the Capital Flow
  // zone (see the h += oi-flow-section block in renderDashboard above),
  // but this incremental patch still finds it fine via getElementById
  // regardless of DOM position. Previously this card only ever rebuilt on a full
  // renderDashboard() pass (it rendered near the Executive grid, outside
  // this incremental refresh function entirely) — it needs the same
  // per-tick treatment as its neighboring cards or it would visibly lag
  // behind them between full rebuilds.
  patchOuterHtmlIfChanged('fiidii-summary-card', () => buildFiiDiiSummaryCard(_data), {
    guardKey: 'fiiDiiSummary', bindGuard: true
  });

  // 4c. Institutional Activity Crux — same outerHTML-diff treatment; without
  // this it would only ever refresh on a full renderDashboard() rebuild,
  // same staleness gap the chain-summary/OI-flow/Greeks cards above it were
  // fixed for.
  // FIX: same freeze bug as the chain-summary-card guard above — this
  // card's "Strike Detail Report →" button was being destroyed mid-click
  // by the unconditional outerHTML swap on every tick. guardKey skips the
  // rebuild while a click here is in flight.
  patchOuterHtmlIfChanged('inst-activity-summary-card', () => app.exec.buildInstitutionalActivitySummaryCard(_data), {
    guardKey: 'instActivity', bindGuard: true
  });

  // NOTE: the old "6. IV Surface" refresh block (targeting the now-removed
  // #sec-iv .section-card) is gone — its content had merged into the
  // iv-hv-skew-detail-card, which is itself now removed (2026-08-01) as a
  // duplicate of Advanced Analytics' "IV Rank Details" card. Nothing
  // needs a Tier-3 refresh block anymore; ATM Greeks refreshes as part of
  // greeks-alerts-card below (4b), and Advanced Analytics refreshes as
  // part of advanced-analytics-card further down.

  // ATM Greeks — no longer a separate Tier-3 collapsible (2026-08-01);
  // folded into the Δ Greeks / Net GEX exec card, so it refreshes as part
  // of that card's own outerHTML-diff block (4b, buildGreeksAlertsHtml)
  // instead of needing its own here.

  // Volatility — new standalone Tier-3-style collapsible (IA redesign
  // step 7, first Advanced Analytics sub-card extracted out), same
  // open-state preservation as its siblings so an expanded panel
  // survives a tick refresh instead of collapsing out from under the
  // user.
  patchOuterHtmlIfChanged('volatility-card', () => app.chain.buildVolatilityHtml(_data), {
    preserveState: (el) => el.hasAttribute('open'),
    restoreState: (fresh, wasOpen) => { if(wasOpen) fresh.setAttribute('open',''); }
  });

  // Probability — second standalone Tier-3-style collapsible extracted
  // out of Advanced Analytics (IA redesign step 7, second pass — see
  // probability-view.js), same open-state preservation as Volatility
  // above.
  patchOuterHtmlIfChanged('probability-card', () => app.chain.buildProbabilityHtml(_data), {
    preserveState: (el) => el.hasAttribute('open'),
    restoreState: (fresh, wasOpen) => { if(wasOpen) fresh.setAttribute('open',''); }
  });

  // Scenario Analysis — third standalone Tier-3-style collapsible
  // extracted out of Advanced Analytics (IA redesign step 7, third pass
  // — see scenario-analysis-view.js), same open-state preservation as
  // Volatility/Probability above.
  patchOuterHtmlIfChanged('scenario-analysis-card', () => app.chain.buildScenarioAnalysisHtml(_data), {
    preserveState: (el) => el.hasAttribute('open'),
    restoreState: (fresh, wasOpen) => { if(wasOpen) fresh.setAttribute('open',''); }
  });
  // Scenario P&L bar chart — outerHTML swap above destroys the canvas
  // whenever the card's HTML changed, so rebind/redraw the Chart.js
  // instance same as updateGreeksMoneynessChart below. Safe to call even
  // when the card didn't change this tick: the signature check inside
  // updateScenarioPnlChart skips the redraw when the numbers are the same.
  if (window.updateScenarioPnlChart) window.updateScenarioPnlChart(_data);
  // Card is <details> collapsed-by-default (unlike Greeks by Moneyness,
  // which is always visible), so also (re)bind the toggle listener that
  // forces a resize + redraw once the person actually opens it — the
  // outerHTML swap above just replaced the element, dropping any
  // previously-bound listener. No-op if already bound this element.
  if (window.bindScenarioPnlChartToggle) window.bindScenarioPnlChartToggle();

  // Advanced Analytics — fourth Tier-3-style collapsible, same open-state
  // preservation as its siblings above (it re-derives all remaining
  // sub-cards from live data every tick, so an open panel never goes
  // stale).
  patchOuterHtmlIfChanged('advanced-analytics-card', () => app.chain.buildAdvancedAnalyticsHtml(_data), {
    preserveState: (el) => el.hasAttribute('open'),
    restoreState: (fresh, wasOpen) => { if(wasOpen) fresh.setAttribute('open',''); }
  });

  // ── 7. Greeks & GEX panels ───────────────────────────────────────────────
  if(isModalOpen('greeks-dashboard-modal')) renderGreeksGex(_grkView);

  // ── 7b. IV Surface modal ─────────────────────────────────────────────────
  // See the matching BUGFIX note in renderDashboard's post-render block —
  // this was never actually called from here before, so a range-button
  // click (switchChainRange -> _rerenderChainPanels, not a full
  // renderDashboard rebuild) left the modal showing the old range's chain
  // if it happened to be open at the time.
  if(isModalOpen('iv-surface-modal')) this.renderIvSurfaceModal();

  // ── 8. OI Velocity panel ─────────────────────────────────────────────────
  renderVelocity(_velWin);

  // ── 9. Institutional F&O Simulator + Scenario Controls ─────────────────────
  // This was missing entirely: expiry switches only ever refreshed the 8
  // panels above, so the simulator's GEX chart/stats/table/vol-grid kept
  // showing whatever expiry was loaded first, and moving the Scenario
  // Control sliders had no visible effect until the next full page reload.
  if (document.getElementById('sim-gex-canvas')) simInit();
  // Keep an open PDS-03 report current from the canonical payload tick,
  // independently of whether the simulator panel exists or is active.
  if (app.strikeDetail) app.strikeDetail.refresh();

  // ── 10. Executive dashboard (Market Health / Market Story / Top Movers) ────
  // Same gap as above — this block was only ever built once, during the
  // full renderDashboard() pass, so GEX/PCR/theta figures in these three
  // cards went stale after an expiry-only switch.
  // This wrapper sits alongside the Chain Snapshot card's nav-card-header
  // link — reuses the 'chainSummary' guard key so it doesn't get replaced
  // out from under that click either. Doesn't bind its own guard (no
  // action buttons of its own), only checks the one chain-summary-card
  // already owns.
  patchOuterHtmlIfChanged('exec-section-wrap', () => {
    // computeNetGEX (metrics.js, IA redesign step 6) — `greeks` here is
    // the same visible-range array as renderDashboard's, so this stays
    // consistent with what the Greeks/Net GEX Alerts card itself shows.
    _data.totalGEX = computeNetGEX(greeks);
    return renderExecutiveDashboard(_data);
  }, { guardKey: 'chainSummary' });

  if (window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(_data);
  const atmGreekText = {
    'atm-greeks-heading': `ATM ${fmtI(_data.atm)}`,
    'atm-greek-delta': fmtN(_data.atmDelta,4),
    'atm-greek-gamma': fmtN(_data.atmGamma,4),
    'atm-greek-theta': fmtN(_data.atmTheta,2),
    'atm-greek-vega': fmtN(_data.atmVega,2)
  };
  Object.entries(atmGreekText).forEach(([id,value]) => {
    const el = document.getElementById(id);
    if(el) el.textContent = value;
  });
};
