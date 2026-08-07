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
    this.timerMins = Config.refresh.defaultAutoRefreshMins;
    this.renderScheduled = false;
    this.store = new MarketStore();
    this.wsManager = new WSManager(Config.ws.url);
    this.feedStatusTimer = null;
    this._setFeedStatus('CONNECTING');
    this.wsManager.on('open', () => {
      err('');
      const dot=$i('ws-status'); if(dot) dot.style.background='var(--warn)';
      // Socket open is not the same thing as a coherent market snapshot.
      // Stay RECOVERING until the first message actually arrives.
      this._setFeedStatus('RECOVERING');
    });
    this.wsManager.on('close', () => {
      const dot=$i('ws-status'); if(dot) dot.style.background='var(--red)';
      this._setFeedStatus('DISCONNECTED', 'WebSocket closed');
    });
    // Raw wire message -> MarketStore interprets it -> 'change' fires with
    // the merged state, which is what actually drives a re-render. Feed
    // freshness is updated BEFORE ingest so the top bar built by the same
    // tick already renders LIVE rather than one tick behind.
    this.wsManager.on('message', (raw) => {
      this._markFeedMessage();
      this.store.ingest(raw);
    });
    this.feedStatusTimer = setInterval(() => this._checkFeedFreshness(), 1000);
    this.store.on('change', (state) => this.updateDashboard(state));
    // Tracks which symbol's DOM is currently built, so scheduleRender() can
    // force a full rebuild on a scrip switch instead of patching in place —
    // see the notYetBuilt/symbolChanged check in scheduleRender().
    this.lastRenderedSymbol = null;
  }

  _setFeedStatus(status, reason){
    const prev = AppState.feedState || {};
    AppState.feedState = {
      status: status,
      quality: prev.quality || 'UNKNOWN',
      missing: prev.missing || [],
      marketSession: prev.marketSession || 'UNKNOWN',
      lastMessageAt: prev.lastMessageAt || null,
      lastStatusAt: Date.now(),
      reason: reason || '',
    };
    this._updateFeedStatusDom();
    if (window.eventBus) window.eventBus.emit('feed:status', AppState.feedState);
  }

  _markFeedMessage(){
    const now = Date.now();
    const prev = AppState.feedState || {};
    AppState.feedState = {
      status: 'LIVE',
      quality: prev.quality || 'UNKNOWN',
      missing: prev.missing || [],
      marketSession: prev.marketSession || 'UNKNOWN',
      lastMessageAt: now,
      lastStatusAt: prev.status === 'LIVE' ? (prev.lastStatusAt || now) : now,
      reason: '',
    };
    this._updateFeedStatusDom();
  }

  _updateDataQuality(state){
    const prev = AppState.feedState || {};
    const missing = [];
    if (!state || !Array.isArray(state.chain) || !state.chain.length) missing.push('chain');
    if (!state || !Array.isArray(state.greeks) || !state.greeks.length) missing.push('greeks');
    if (!state || !state.decision) missing.push('decision');
    AppState.feedState = {
      ...prev,
      quality: missing.length ? 'PARTIAL' : 'FULL',
      missing,
      marketSession: (state && state.marketSession) || prev.marketSession || 'UNKNOWN',
    };
    this._updateFeedStatusDom();
    if (window.eventBus && (prev.quality !== AppState.feedState.quality || prev.marketSession !== AppState.feedState.marketSession)) {
      window.eventBus.emit('feed:status', AppState.feedState);
    }
  }

  _checkFeedFreshness(){
    const fs = AppState.feedState || {};
    if (!fs.lastMessageAt) return;
    if (fs.status === 'DISCONNECTED' || fs.status === 'CONNECTING' || fs.status === 'RECOVERING') return;
    const age = Date.now() - fs.lastMessageAt;
    const staleAfter = (Config.ws && Config.ws.staleAfterMs) || 12000;
    if (age > staleAfter && fs.status !== 'STALE') {
      this._setFeedStatus('STALE', `No feed message for ${Math.floor(age/1000)}s`);
    } else if (fs.status === 'STALE') {
      // Keep the age label moving without causing a Dashboard re-render.
      this._updateFeedStatusDom();
    }
  }

  _updateFeedStatusDom(){
    const el = $i('feed-status-pill');
    if (!el) return;
    const fs = AppState.feedState || {status:'CONNECTING'};
    const status = fs.status || 'CONNECTING';
    const session = fs.marketSession || 'UNKNOWN';
    let visualStatus = status;
    let label = status;
    if (session === 'HOLIDAY') {
      visualStatus = 'HOLIDAY';
      label = status === 'DISCONNECTED' ? 'HOLIDAY · OFFLINE' : 'HOLIDAY';
    } else if (session === 'MARKET_CLOSED') {
      visualStatus = 'MARKET_CLOSED';
      label = status === 'DISCONNECTED' ? 'MARKET CLOSED · OFFLINE' : 'MARKET CLOSED';
    } else if (status === 'LIVE' && fs.quality === 'PARTIAL') {
      visualStatus = 'PARTIAL';
      label = 'PARTIAL';
    } else if (status === 'STALE' && fs.lastMessageAt) {
      label += ` ${Math.max(1, Math.floor((Date.now()-fs.lastMessageAt)/1000))}s`;
    }
    el.textContent = label;
    el.dataset.status = visualStatus.toLowerCase().replace('_','-');
    const missingTxt = fs.missing && fs.missing.length ? ` Missing: ${fs.missing.join(', ')}.` : '';
    el.title = (fs.reason || (fs.lastMessageAt ? `Last feed message ${new Date(fs.lastMessageAt).toLocaleTimeString()}` : status)) + missingTxt;
  }

  connectWebSocket(url){
    this._setFeedStatus('CONNECTING');
    this.wsManager.connect(url);
  }

  // Called with the already-merged state (MarketStore.ingest ran before
  // emitting 'change') — this function is now pure side-effects: no more
  // branching on msg.type here, that lives in WSManager.
  updateDashboard(state){
  AppState.wsState = state;
  if(!AppState.wsState) return;
  this._updateDataQuality(AppState.wsState);

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

  // applyExpirySelection() call removed (dead code — see chain-helpers.js):
  // expiry switching is handled by ChainView.onExpiryChange reconnecting
  // the WebSocket with ?expiry=..., not by splicing an alternate expiry's
  // chain into this tick's payload. This call was always a no-op since
  // app.chain.selectedExpiry (_selectedExpiry) is never written anywhere.

  // Feed the live price chart from this same tick's spot value. The
  // chart engine itself (price-chart.js) no longer loads on this page —
  // it lives on the standalone price-chart.html tab now — so this just
  // broadcasts the tick over BroadcastChannel('pc-live-sync') via
  // PriceChartPanel; that tab's own price-chart-standalone.js calls
  // priceChart.addTick() on the receiving end using the same client-side
  // timestamp approach as before (no timestamp field exists on the
  // payload today — fine for a live scrolling chart, just not a true
  // exchange-timestamped tape).
  //
  // VWAP intentionally NOT sent for the index spot chart: NIFTY/etc. are
  // computed composite index levels, not traded instruments — they have
  // no volume or traded value of their own. allIndices' Value/Volume
  // fields are the SUM across the index's individual constituent stocks
  // (Reliance, HDFC Bank, ...), so Value/Volume is really "average traded
  // price across those constituent shares" — a real number, just not the
  // index's VWAP, and not comparable to the index level at all (hence it
  // showing ~900 next to a ~24,000 spot). If a real index-level VWAP is
  // wanted later, it needs to come from NIFTY FUTURES turnover/volume
  // (see fetch_nifty_futures in market_api.py) or SmartAPI's own volume
  // field on the index token, not this basket aggregate.
  if(AppState.wsState.spot != null) {
    panelManager.get('priceChart').pushTick(
      AppState.wsState.spot, AppState.wsState.symbol,
      AppState.wsState.spotChange, AppState.wsState.spotChgPct
    );
  }

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
  // ── WATCHDOG FIX ──
  // requestAnimationFrame alone silently stalls the entire live-update
  // pipeline (top bar, Decision Box, chain panels — everything gated
  // behind scheduleRender) whenever the browser throttles/suspends rAF
  // for this tab. Chromium does this for any tab that isn't the active
  // foreground tab UNLESS DevTools is attached to it, which is why this
  // used to "work fine with F12 open" and freeze solid with it closed —
  // WS messages kept arriving and MarketStore kept merging state the
  // whole time (that path isn't rAF-gated), but nothing ever got drawn
  // because the only thing turning state into DOM updates was stuck
  // waiting on a clamped/paused rAF callback.
  //
  // Fix: race the real rAF callback against a plain setTimeout watchdog.
  // Whichever fires first runs the render and cancels the other, so a
  // throttled rAF (typically clamped to ~1fps, not fully frozen) just
  // means the timeout wins most ticks instead — not that rendering stops.
  const runOnce = () => {
    if (!this.renderScheduled) return; // already run by the other trigger
    this.renderScheduled = false;
    if (rafId != null) cancelAnimationFrame(rafId);
    if (timeoutId != null) clearTimeout(timeoutId);
    doRender();
  };
  const rafId = requestAnimationFrame(runOnce);
  const timeoutId = setTimeout(runOnce, 250);
  const doRender = () => {
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
      if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
      if (window.renderAlgoStatusPanel) window.renderAlgoStatusPanel(AppState.wsState);
      if (window.renderReconciliationAlerts) renderReconciliationAlerts(AppState.wsState);
      return;
    }
    this.lastRenderedSymbol = AppState.wsState.symbol || _lastRenderedSymbol;
    _data = AppState.wsState;
    if (window._afterRenderStratPayoff) _afterRenderStratPayoff();
    if (window._rerenderChainPanels) app.chain._rerenderChainPanels();
    // Decision Engine box + top-bar spot/badge/DTE strip are cheap to patch
    // in place too, so they stay live without rebuilding their containers.
    if (window.patchTopBarAndDecision) patchTopBarAndDecision(AppState.wsState);
    // Paper trading panel — lives outside #dashboard (self-mounted, see
    // bottom of file), so it's never touched by the rebuild above and just
    // needs its own cheap patch-in-place call here, same pattern as the
    // other panels on this line.
    if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
    // Algo status panel — same self-mounted, outside-#dashboard treatment
    // as the paper trading panel above. Its own setHtmlIfChanged guard
    // (algo-status.js) keeps this a no-op on ticks where algoStatus
    // itself hasn't changed since algo_status_loop() broadcasts on its
    // own slow independent timer, not tick-cadence.
    if (window.renderAlgoStatusPanel) window.renderAlgoStatusPanel(AppState.wsState);
    // Reconciliation alerts (risk/position_reconciler.py) — same
    // self-mounted, outside-#dashboard treatment, toast-based rather than
    // a persistent panel patch, so its own internal dedupe (algo-status.js's
    // _reconSeenTs) is what keeps this a no-op once an alert's already
    // been shown, not a setHtmlIfChanged guard like the panels above.
    if (window.renderReconciliationAlerts) renderReconciliationAlerts(AppState.wsState);
  };
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
    Logger.error('DataService', 'renderDashboard failed', e);
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