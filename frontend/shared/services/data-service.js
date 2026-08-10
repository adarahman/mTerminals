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
    // market freshness is updated BEFORE ingest so the top bar built by the
    // same tick already renders LIVE rather than one tick behind. Auxiliary
    // messages (portfolio, orders, funds, algoStatus, indexQuotes) prove the
    // transport is active but must not keep a frozen market snapshot LIVE.
    this.wsManager.on('message', (raw) => {
      this._markTransportMessage();
      if (this._isMarketSnapshotMessage(raw)) this._markFeedMessage();
      this.store.ingest(raw);
    });
    this.feedStatusTimer = setInterval(() => this._checkFeedFreshness(), 1000);
    this.store.on('change', (state, change) => this.updateDashboard(state, change));
    this.store.on('baselineMismatch', () => {
      this._setFeedStatus('RECOVERING', 'Refreshing incompatible market snapshot');
      this.wsManager.connect(undefined, true);
    });
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
      lastTransportAt: prev.lastTransportAt || null,
      lastStatusAt: Date.now(),
      reason: reason || '',
      pipelineDelayed: prev.pipelineDelayed || false,
      pipelineReason: prev.pipelineReason || '',
    };
    this._updateFeedStatusDom();
    if (window.eventBus) window.eventBus.emit('feed:status', AppState.feedState);
  }

  _isMarketSnapshotMessage(raw){
    // A missing envelope is the legacy plain full-snapshot shape. Only the
    // versioned full/delta stream owns chain/Greeks/decision freshness.
    return !raw || !raw.type || raw.type === 'full' || raw.type === 'delta';
  }

  _applyPipelineStatus(status){
    if(!status) return;
    if(status.status === 'DELAYED'){
      const prev = AppState.feedState || {};
      const pipelineReason = status.reason || 'Analytics delayed; live prices continue';
      AppState.feedState = {
        ...prev,
        pipelineDelayed: true,
        pipelineReason,
        // Pipeline health is supplementary. It must not overwrite the
        // market transport state or its visible reason. The pipeline can
        // alternate DELAYED/LIVE every pass, which must not make the Feed
        // header flash or change size while WebSocket prices remain live.
        reason: prev.reason === prev.pipelineReason ? '' : prev.reason,
      };
      this._updateFeedStatusDom();
    } else if(status.status === 'LIVE'){
      const prev = AppState.feedState || {};
      AppState.feedState = {
        ...prev,
        pipelineDelayed: false,
        pipelineReason: '',
        reason: prev.reason === prev.pipelineReason ? '' : prev.reason,
      };
      this._updateFeedStatusDom();
    }
  }

  _markTransportMessage(){
    const prev = AppState.feedState || {};
    AppState.feedState = { ...prev, lastTransportAt: Date.now() };
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
      lastTransportAt: prev.lastTransportAt || now,
      lastStatusAt: prev.status === 'LIVE' ? (prev.lastStatusAt || now) : now,
      reason: '',
      pipelineDelayed: !!prev.pipelineDelayed,
      pipelineReason: prev.pipelineReason || '',
    };
    this._updateFeedStatusDom();
    if (window.eventBus && prev.status !== AppState.feedState.status) {
      window.eventBus.emit('feed:status', AppState.feedState);
    }
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
    const staleAfter = (Config.ws && Config.ws.staleAfterMs) || 30000;
    if (age > staleAfter && fs.status !== 'STALE') {
      this._setFeedStatus('STALE', `No market snapshot for ${Math.floor(age/1000)}s`);
    } else if (fs.status === 'STALE') {
      // Keep the age label moving without causing a Dashboard re-render.
      this._updateFeedStatusDom();
    }
  }

  _updateFeedStatusDom(){
    const el = $i('feed-status-pill');
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
    const missingTxt = fs.missing && fs.missing.length ? ` Missing: ${fs.missing.join(', ')}.` : '';
    const rawReasonText = fs.reason || (fs.quality === 'PARTIAL' ? missingTxt.trim() : '');
    // The MARKET CLOSED / HOLIDAY badge already communicates the normal
    // session state. Repeating "Market session is market closed" beside
    // it wastes top-bar width; preserve only actionable/non-redundant
    // reasons here. The full reason remains in the badge title above.
    const redundantSessionReason = (fs.marketSession === 'MARKET_CLOSED' || fs.marketSession === 'HOLIDAY')
      && /market session|market closed|holiday/i.test(rawReasonText);
    const reasonText = redundantSessionReason ? '' : rawReasonText;
    if (el) {
      el.textContent = label;
      el.dataset.status = visualStatus.toLowerCase().replace('_','-');
      const pipelineDetail = fs.pipelineDelayed && fs.pipelineReason ? ` Analytics: ${fs.pipelineReason}.` : '';
      el.title = (fs.reason || (fs.lastMessageAt ? `Last market snapshot ${new Date(fs.lastMessageAt).toLocaleTimeString()}` : status)) + missingTxt + pipelineDetail;
    }
    const reasonEl = $i('feed-status-reason');
    if (reasonEl) {
      reasonEl.textContent = reasonText;
      reasonEl.hidden = !reasonText;
    }
  }

  connectWebSocket(url){
    this._setFeedStatus('CONNECTING');
    this.wsManager.connect(url);
  }

  // Called with the already-merged state (MarketStore.ingest ran before
  // emitting 'change') — this function is now pure side-effects: no more
  // branching on msg.type here, that lives in WSManager.
  updateDashboard(state, change){
  AppState.wsState = state;
  if(!AppState.wsState) return;
  const messageType = change && change.messageType;

  // Generic side-channel messages share the socket/store but do not change
  // chain, Greeks, decisions, charts, or market summaries. Route each one
  // directly to its owner instead of scheduling the entire dashboard pass.
  if(messageType === 'portfolio' || messageType === 'orders' || messageType === 'funds'){
    if(window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
    return;
  }
  if(messageType === 'pipelineStatus'){
    this._applyPipelineStatus(AppState.wsState.pipelineStatus);
    return;
  }
  if(messageType === 'algoStatus'){
    if(window.renderAlgoStatusPanel) window.renderAlgoStatusPanel(AppState.wsState);
    return;
  }
  if(messageType === 'reconciliationAlert'){
    if(window.renderReconciliationAlerts) renderReconciliationAlerts(AppState.wsState);
    return;
  }
  if(messageType === 'indexQuotes'){
    if(window.patchIndexTicker) window.patchIndexTicker(AppState.wsState);
    return;
  }
  this._updateDataQuality(AppState.wsState);

  // Keep the native Price Chart buffer current even while its modal is
  // closed, so opening it never starts from an empty live series.
  if(window.priceChart && AppState.wsState.spot != null){
    window.priceChart.addTick(AppState.wsState.spot, Date.now(), null);
  }

  // applyExpirySelection() call removed (dead code — see chain-helpers.js):
  // expiry switching is handled by ChainView.onExpiryChange reconnecting
  // the WebSocket with ?expiry=..., not by splicing an alternate expiry's
  // chain into this tick's payload. This call was always a no-op since
  // app.chain.selectedExpiry (_selectedExpiry) is never written anywhere.

  // Keep canonical Option Chain rows ready for in-dashboard drill-downs.
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
