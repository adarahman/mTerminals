// ============================================================
// data-service.js
// Phase 1 bootstrap cleanup (see master optimization prompt, Task
// "Dashboard bootstrap cleanup"): dashboard.js is meant to hold ONLY app
// init/wiring/coordination now. DataService owns the "state" and
// "websocket" responsibilities (file/paste loading, auto-refresh timer,
// WSManager/MarketStore wiring, and the coalesced-render scheduler) —
// pulled out verbatim into its own file, same treatment ws-manager.js/
// market-store.js already got.
//
// WSManager (ws-manager.js) owns only the socket lifecycle. MarketStore
// (market-store.js) owns the merged state and the full/delta/generic wire
// interpretation — see Phase 1 architecture split, master optimization
// prompt. Both must be loaded before this file, since DataService's
// constructor below does `new WSManager(...)` and `new MarketStore()`.
// This file itself must load before dashboard.js, since dashboard.js's
// App class does `new DataService()` at parse time (via `const app = new
// App()`). See DashboardPro.html script order.
// ============================================================

class DataService {
  constructor() {
    this.fsaHandle = null;
    this.legacyFile = null;
    this.data = null;
    this.autoRefreshTimer = null;
    this.countdownTimer = null;
    this.timerMins = 5;
    this.renderScheduled = false;
    this.store = new MarketStore();
    this.wsManager = new WSManager(`ws://${location.host}/ws`);
    this.wsManager.on('open', () => {
      err('');
      const dot=$i('ws-status'); if(dot) dot.style.background='var(--green)';
    });
    this.wsManager.on('close', () => {
      const dot=$i('ws-status'); if(dot) dot.style.background='var(--red)';
    });
    // Raw wire message -> MarketStore interprets it -> 'change' fires with
    // the merged state, which is what actually drives a re-render.
    this.wsManager.on('message', (raw) => this.store.ingest(raw));
    this.store.on('change', (state) => this.updateDashboard(state));
    // Tracks which symbol's DOM is currently built, so scheduleRender() can
    // force a full rebuild on a scrip switch instead of patching in place —
    // see the notYetBuilt/symbolChanged check in scheduleRender().
    this.lastRenderedSymbol = null;
  }

  connectWebSocket(url){
    this.wsManager.connect(url);
  }

  // Called with the already-merged state (MarketStore.ingest ran before
  // emitting 'change') — this function is now pure side-effects: no more
  // branching on msg.type here, that lives in WSManager.
  updateDashboard(state){
  AppState.wsState = state;
  if(!AppState.wsState) return;

  // OI dashboard iframe / popup — only push when the panel is actually
  // open. Previously every SmartAPI/REST tick structured-cloned the full
  // dashboard payload into the iframe (and optional popup) even when the
  // OI modal was closed, which is pure main-thread cost for no UI gain.
  // Coalesce to one postMessage per animation frame via scheduleRender's
  // rAF, but only emit if a live consumer is present.
  const oiFrame = document.getElementById("oi-modal-iframe");
  const oiFrameLive = oiFrame && oiFrame.contentWindow
    && oiFrame.offsetParent !== null; // hidden/display:none → offsetParent null
  const oiPopupLive = typeof _oiDashboardWin !== 'undefined'
    && _oiDashboardWin && !_oiDashboardWin.closed;
  if (oiFrameLive || oiPopupLive) {
    if (!this._oiPostScheduled) {
      this._oiPostScheduled = true;
      const self = this;
      requestAnimationFrame(function(){
        self._oiPostScheduled = false;
        if(!AppState.wsState) return;
        const msg = { type: "OI_DASHBOARD_DATA", payload: AppState.wsState };
        const frame = document.getElementById("oi-modal-iframe");
        if (frame && frame.contentWindow && frame.offsetParent !== null) {
          frame.contentWindow.postMessage(msg, "*");
        }
        if (typeof _oiDashboardWin !== 'undefined' && _oiDashboardWin && !_oiDashboardWin.closed) {
          _oiDashboardWin.postMessage(msg, "*");
        }
      });
    }
  }

  // ── SINGLE DATA SOURCE / SINGLE REFRESH PIPELINE ──
  // Apply whatever expiry the global #expirySelect dropdown currently has
  // selected to this tick's raw payload BEFORE the dense Option Chain table
  // reads it, so the chain never flashes back to the primary expiry on a
  // live tick. This is the same helper renderDashboard() uses for the rest
  // of the dashboard (see applyExpirySelection below), so both the KPI/
  // analytics panels and the Option Chain table stay driven by one piece of
  // state (_selectedExpiry) off one payload.
  applyExpirySelection(AppState.wsState, _selectedExpiry);

  // Feed the live price chart from this same tick's spot value. Client-side
  // timestamp (not a backend one) since no timestamp field exists on the
  // payload today — fine for a live scrolling chart, just not a true
  // exchange-timestamped tape.
  //
  // VWAP intentionally NOT computed for the index spot chart: NIFTY/etc.
  // are computed composite index levels, not traded instruments — they
  // have no volume or traded value of their own. allIndices' Value/Volume
  // fields are the SUM across the index's individual constituent stocks
  // (Reliance, HDFC Bank, ...), so Value/Volume is really "average traded
  // price across those constituent shares" — a real number, just not the
  // index's VWAP, and not comparable to the index level at all (hence it
  // showing ~900 next to a ~24,000 spot). If a real index-level VWAP is
  // wanted later, it needs to come from NIFTY FUTURES turnover/volume
  // (see fetch_nifty_futures in market_api.py) or SmartAPI's own volume
  // field on the index token, not this basket aggregate.
  if(AppState.wsState.spot != null) priceChart.addTick(AppState.wsState.spot, Date.now(), null);

  // Drive the native Option Chain table/right panel straight off this same
  // tick — no separate WebSocket, no postMessage relay, no iframe.
  // refreshView() itself no-ops (checks for #tbody) when this page doesn't
  // have the dense chain markup mounted, so this is always safe to call
  // unconditionally.
  app.chainDense.refreshView(AppState.wsState);

  // Multiple WS messages (e.g. spot+oi+greeks) often arrive back-to-back
  // for the same logical tick. Coalesce them into a single render per
  // animation frame instead of doing a full rebuild for each one.
  scheduleRender();
}

  scheduleRender(){
  if(this.renderScheduled) return;
  this.renderScheduled = true;
  requestAnimationFrame(()=> {
    this.renderScheduled = false;
    if(!AppState.wsState) return;
    // ── FLICKER FIX ──
    // Every live tick used to go through parseAndRender() -> renderDashboard(),
    // which nukes and rebuilds the ENTIRE #dashboard subtree from a fresh
    // HTML string every time — that's what made every card flicker on every
    // tick. OI Flow never flickered because it was always patched in place
    // via #oi-flow-body.innerHTML only. The fix: give every tick that same
    // treatment. _rerenderChainPanels() already patches the chain table, DTE,
    // right panel, OI buildup/movers, IV surface, Greeks/GEX, OI velocity,
    // Simulator, and exec-grid in place, without touching unrelated DOM.
    // A full rebuild now only happens once, on the very first tick (when
    // #dashboard is still empty) or when the scrip itself changes.
    const dashEl = $i('dashboard');
    // Also treat "present but still hidden" as not-yet-built: #dashboard can
    // ship with non-empty skeleton/placeholder markup baked into the page
    // before any live data arrives, which made the old (innerHTML-only)
    // check false on tick #1 — skipping parseAndRender() entirely, which is
    // the ONLY code that ever flips #dashboard's display from none to block.
    // Net effect was the whole dashboard silently staying display:none
    // forever on the live-WS path even once real data was flowing.
    const notYetBuilt = !dashEl || !dashEl.innerHTML.trim() || dashEl.style.display === 'none';
    // ── SCRIP-SWITCH FIX ──
    // Symptom: switching symbol (NIFTY -> BANKNIFTY etc, e.g. reconnecting
    // to a different --symbol backend) updated the expiry dropdown and
    // chain table correctly (both driven through _rerenderChainPanels /
    // applyExpirySelection off the fresh AppState.wsState), but the Decision Engine
    // box kept showing the OLD scrip's bias/confidence/strategy until a
    // manual page refresh. Root cause: patchTopBarAndDecision() only ever
    // patches individual fields/DOM nodes in place — it was never designed
    // to detect "this is an entirely different instrument now", so nothing
    // forced it to redraw fields it assumes change rarely. Rather than
    // patch that assumption inside every incremental-update function, we
    // detect the scrip change once, here, at the dispatch point, and fall
    // back to a full rebuild — the same one already used for the very
    // first tick — which rebuilds the Decision Engine box (and everything
    // else) from scratch off the new symbol's data.
    const symbolChanged = AppState.wsState.symbol && this.lastRenderedSymbol && AppState.wsState.symbol !== this.lastRenderedSymbol;
    if (notYetBuilt || symbolChanged) {
      this.lastRenderedSymbol = AppState.wsState.symbol || this.lastRenderedSymbol;
      parseAndRender(JSON.stringify(AppState.wsState));
      // parseAndRender() just replaced #dashboard's entire innerHTML, which
      // wipes out the price-chart panel since it lives inside #dashboard —
      // remount it into the freshly-rebuilt container right here.
      if (window.priceChart) priceChart.render();
      if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
      return;
    }
    this.lastRenderedSymbol = AppState.wsState.symbol || _lastRenderedSymbol;
    _data = AppState.wsState;
    if (window._afterRenderStratPayoff) _afterRenderStratPayoff();
    if (window._rerenderChainPanels) app.chain._rerenderChainPanels();
    // Decision Engine box + top-bar spot/badge/DTE strip are cheap to patch
    // in place too, so they stay live without rebuilding their containers.
    if (window.patchTopBarAndDecision) patchTopBarAndDecision(AppState.wsState);
    // Live price chart — cheap redraw onto the existing canvas surface
    // (sizeCanvasIfChanged only resets it when the on-screen size changed),
    // so this rides the same per-frame coalescing as the other panels above.
    if (window.priceChart) priceChart.render();
    // Paper trading panel — lives outside #dashboard (self-mounted, see
    // bottom of file), so it's never touched by the rebuild above and just
    // needs its own cheap patch-in-place call here, same pattern as the
    // other panels on this line.
    if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
  });
}

  async triggerFile(){
  if(window.showOpenFilePicker){
    try{
      const [h]=await window.showOpenFilePicker({types:[{description:'JSON',accept:{'application/json':['.json']}}],multiple:false});
      _fsaHandle=h;_legacyFile=null;err('');
      await _readAndRender();
      startAutoRefresh(_timerMins);
    }catch(e){
      if(e.name!=='AbortError') err('File open error: '+e.message);
    }
  }else{
    $i('file-input').click();
  }
}

  handleFile(e){
  const f=e.target.files[0];
  if(!f)return;
  _legacyFile=f;_fsaHandle=null;err('');
  const r=new FileReader();
  r.onload=ev=>{parseAndRender(ev.target.result);startAutoRefresh(_timerMins);};
  r.onerror=()=>err('File read error.');
  r.readAsText(f,'utf-8');
}

  triggerPaste(){
  $i('paste-area').style.display='block';
  $i('load-go').style.display='inline-block';
  $i('paste-area').focus();
  err('');
}

  loadFromPaste(){
  parseAndRender($i('paste-area').value.trim());
}

  async _readAndRender(){
  try{
    let txt='';
    if(_fsaHandle){
      const file=await _fsaHandle.getFile();
      txt=await file.text();
    }else if(_legacyFile){
      txt=await _legacyFile.text();
    }else{
      err('No file loaded — use Open file first.');
      return;
    }
    if(!txt || txt.trim()===''){
      err('File is empty.');
      return;
    }
    parseAndRender(txt);
    err('');
  }catch(e){
    err('Read error: '+e.message);
  }
}

  async refreshDashboardFromExport(){
  if(!_fsaHandle && !_legacyFile){
    await triggerFile();
    return;
  }
  await _readAndRender();
}

  startAutoRefresh(mins=5){
  if(_autoRefreshTimer) clearInterval(_autoRefreshTimer);
  if(_countdownTimer) clearInterval(_countdownTimer);
  
  const intervalMs = mins * 60 * 1000;
  let remaining = intervalMs;
  
  function tick(){
    remaining -= 1000;
    if(remaining <= 0) remaining = intervalMs;
    const m = Math.floor(remaining / 60000);
    const s = Math.floor((remaining % 60000) / 1000);
    const timeStr = m + ':' + (s < 10 ? '0' : '') + s;
    
    const cd = document.getElementById('countdown-range');
    if(cd) cd.textContent = timeStr;
  }
  
  tick();
  _countdownTimer = setInterval(tick, 1000);
  
  _autoRefreshTimer = setInterval(async() => {
    if((_fsaHandle || _legacyFile) && document.getElementById('dashboard').style.display === 'block'){
      await _readAndRender();
      remaining = intervalMs;
    }
  }, intervalMs);
}

  parseAndRender(raw){
  if(!raw || raw.trim()===''){
    err('No data.');
    return;
  }
  let d;
  try{
    d=JSON.parse(raw);
  }catch(e){
    err('Parse error: '+e.message);
    return;
  }
  d=_fixMojibakeDeep(d);
  if(!d.spot){ err('Missing: spot'); return; }
  if(!d.chain || !d.chain.length){ err('Missing: chain'); return; }
  try{
    renderDashboard(d);
    if (window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(d);
    $i('loader').style.display='none';
    $i('dashboard').style.display='block';
    err('');
  }catch(e){
    err('Render error: '+e.message);
    console.error(e);
  }
}

  async doStickyRefresh(){
  const btn=document.getElementById('sticky-refresh-btn');
  if(btn){btn.classList.add('running');btn.textContent='🔄 Running…';}
  
  const cd=document.getElementById('countdown-range');
  if(cd) cd.textContent=_timerMins+':00';
  
  if(window._autoRefreshTimer){clearInterval(window._autoRefreshTimer);window._autoRefreshTimer=null;}
  if(window._countdownTimer){clearInterval(window._countdownTimer);window._countdownTimer=null;}
  
  try{
    await _readAndRender();
  }catch(e){
    const em=document.getElementById('err-msg');
    if(em) em.textContent='Refresh error: '+e.message;
  }finally{
    startAutoRefresh(_timerMins);
    if(btn){btn.classList.remove('running');btn.textContent='🔄 Refresh';}
  }
}
}
