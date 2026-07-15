function $i(id){return document.getElementById(id);}
function err(m){const el=$i('err-msg');if(el)el.textContent=m;}

// ── FLICKER HELPERS ──────────────────────────────────────────────────────
// OI Flow never flickers because #oi-flow-body is small, text/color-only
// markup. Institutional F&O Simulator and Strategy Payoff flickered because
// every live tick unconditionally rewrote <select> option lists and reset
// <canvas> width/height (which resets the 2D context) even when nothing
// about that particular panel had actually changed. These two helpers make
// "re-render" mean "diff first, touch the DOM only if something changed" —
// the same effect OI Flow gets for free from being simple markup.

// Skip the innerHTML write entirely when the freshly-built HTML string is
// byte-identical to what's already there. Cheap string compare beats a
// guaranteed reflow/repaint on every single WS tick.
function setHtmlIfChanged(el, html){
  if(!el) return;
  if(el.dataset.lastHtml === html) return;
  el.innerHTML = html;
  el.dataset.lastHtml = html;
}

// Only touch canvas.width/height (which clears + resets the 2D context,
// forcing a full repaint) when the on-screen size actually changed. Redraw
// the contents every tick as before, but stop paying the resize cost for
// ticks where the layout hasn't moved — this is what removed the visible
// "flash" from the GEX and Strategy Payoff charts.
function sizeCanvasIfChanged(canvas, wCss, hCss){
  const dpr = window.devicePixelRatio || 1;
  const key = wCss + 'x' + hCss + '@' + dpr;
  const ctx = canvas.getContext('2d');
  if(canvas.dataset.sizeKey === key) return ctx;
  canvas.width  = wCss * dpr;
  canvas.height = hCss * dpr;
  canvas.style.width  = wCss + 'px';
  canvas.style.height = hCss + 'px';
  ctx.setTransform(1,0,0,1,0,0);
  ctx.scale(dpr, dpr);
  canvas.dataset.sizeKey = key;
  return ctx;
}

// ============================================================
// ARCHITECTURE: state + behavior grouped into classes by subsystem.
// Formatting/pure-utility functions below (fmtN, activeAtm, etc.) stay as
// plain functions since they hold no state. Legacy call sites (including
// onclick="..." attributes baked into rendered HTML) keep working via the
// compatibility shims at the bottom of this block.
// ============================================================
// In your dashboard.js or wherever your socket logic lives

class UiControls {
  updateStickyOffsets(){
  const root = document.documentElement.style;
  const topBar = $i('sec-topbar');

  // The vertical nav rail (#sec-nav-bar) is now a fixed left-edge stripe,
  // not stacked above the top-bar, and the index ticker no longer has its
  // own row (it's inline inside the top-bar) — so the top-bar just sits
  // near the top of the viewport instead of being pushed down by either.
  const topBarTop = STICKY_BASE_TOP;
  root.setProperty('--topbar-top', topBarTop + 'px');

  const topBarH = topBar ? topBar.getBoundingClientRect().height : 44;
  const panelTop = topBarTop + topBarH + STICKY_GAP;
  root.setProperty('--panel-top', panelTop + 'px');

  // The old standalone `.head` bar above the chain table was removed
  // (see DashboardPro.html), so there is no local header height to add
  // to the sticky <thead> offset anymore. Force --head-h to 0 instead of
  // leaving it at a stale/default non-zero value — that stale value is
  // what was pushing the sticky thead down into the middle of the table
  // body instead of sitting flush above row 1.
  root.setProperty('--head-h', '0px');
}

  switchTimer(mins,el){
  _timerMins=mins;
  document.querySelectorAll('[id^="timer-btn-"]').forEach(b=>b.classList.remove('active-range'));
  if(el) el.classList.add('active-range');
  startAutoRefresh(mins);
}

  toggleControlSidebar(){
  const el=$i('ctrl-sidebar');
  if(!el) return;
  const opening = !el.classList.contains('open');
  el.classList.toggle('open');
  if(opening){
    // Panel is no longer docked next to a fixed top-right toggle — that
    // button moved into the left #sec-nav-bar rail as "Range/Vel", so
    // position the flyout right next to wherever that button actually is
    // (its section-nav position can shift slightly depending on which
    // sec-btn-* items are visible) instead of a stale hardcoded spot.
    const btn = $i('ctrl-sidebar-toggle-btn');
    if(btn){
      const r = btn.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      // Give it a minimum offset from the nav rail so it never hugs the
      // literal edge (r.right is tiny for the narrow left rail, which was
      // pinning the panel flush against x≈80px — reading as clipped/cut-off
      // text and making it look like a stray row glued under whatever
      // section happened to be at that scroll position instead of a
      // floating flyout next to its own toggle button).
      const EDGE_MARGIN = 16;
      let left = Math.max(r.right + 8, EDGE_MARGIN);
      let top  = r.top;
      // Clamp so the panel can't render partially off-screen once its
      // max-width expands (measure after the 'open' class is applied).
      requestAnimationFrame(()=>{
        const pw = el.offsetWidth || 320;
        const ph = el.offsetHeight || 60;
        if(left + pw > vw - 8) left = Math.max(EDGE_MARGIN, vw - pw - 8);
        if(top + ph > vh - 8) top = Math.max(EDGE_MARGIN, vh - ph - 8);
        el.style.left = left + 'px';
        el.style.top  = top + 'px';
      });
      el.style.left = left + 'px';
      el.style.top  = top + 'px';
    }
  }
}

  secJump(id){
  let el=document.getElementById(id)
    || document.getElementById(id.replace(/-static$/,''))
    || document.getElementById(id+'-static');
  if(!el) return;
  
  const resolvedId=el.id;
  el.scrollIntoView({behavior:'smooth',block:'start'});
  
  // Update nav buttons
  document.querySelectorAll('#sec-nav-bar .sec-btn:not(#sticky-refresh-btn)').forEach(b=>{
    const fn=b.getAttribute('onclick')||'';
    const btnId=(fn.match(/secJump\('([^']+)'\)/)||[])[1]||'';
    const base=s=>s.replace(/-static$/,'');
    b.classList.toggle('active', base(btnId)===base(resolvedId));
  });
  
  el.style.outline='2px solid rgba(51,154,240,0.5)';
  el.style.outlineOffset='2px';
  setTimeout(()=>{el.style.outline='';el.style.outlineOffset='';},900);
}

}

const ui = new UiControls();

// WSManager moved to ws-manager.js (Task 1 modularization, Task 8 —
// WebSocket Manager) — owns the socket lifecycle (connect/reconnect) and
// the wire-format merge (mergeDelta/applyDelta/deepMerge). Loaded earlier
// in DashboardPro.html, right before this file, since DataService's
// constructor below does `new WSManager(...)` at parse time.

class DataService {
  constructor() {
    this.fsaHandle = null;
    this.legacyFile = null;
    this.data = null;
    this.autoRefreshTimer = null;
    this.countdownTimer = null;
    this.timerMins = 5;
    this.renderScheduled = false;
    this.wsManager = new WSManager(`ws://${location.host}/ws`);
    this.wsManager.on('open', () => {
      err('');
      const dot=$i('ws-status'); if(dot) dot.style.background='var(--green)';
    });
    this.wsManager.on('close', () => {
      const dot=$i('ws-status'); if(dot) dot.style.background='var(--red)';
    });
    this.wsManager.on('message', (state) => this.updateDashboard(state));
    // Tracks which symbol's DOM is currently built, so scheduleRender() can
    // force a full rebuild on a scrip switch instead of patching in place —
    // see the notYetBuilt/symbolChanged check in scheduleRender().
    this.lastRenderedSymbol = null;
  }

  connectWebSocket(url){
    this.wsManager.connect(url);
  }

  // Called with the already-merged state (WSManager.mergeDelta ran before
  // emitting 'message') — this function is now pure side-effects: no more
  // branching on msg.type here, that lives in WSManager.
  updateDashboard(state){
  _wsState = state;
  if(!_wsState) return;

  // OI dashboard iframe — kept: OI Dashboard is still a separate embedded
  // page/modal, unlike Option Chain which is now native (see below).
  const oiFrame = document.getElementById("oi-modal-iframe");
  if (oiFrame && oiFrame.contentWindow) {
    oiFrame.contentWindow.postMessage(
        {
            type: "OI_DASHBOARD_DATA",
            payload: _wsState
        },
        "*"
    );
  }

  // Popup fallback window (used when running from file://) — iframes
  // aren't reachable there, so push to the popup directly too.
  if (_oiDashboardWin && !_oiDashboardWin.closed) {
    _oiDashboardWin.postMessage({ type: "OI_DASHBOARD_DATA", payload: _wsState }, "*");
  }

  // ── SINGLE DATA SOURCE / SINGLE REFRESH PIPELINE ──
  // Apply whatever expiry the global #expirySelect dropdown currently has
  // selected to this tick's raw payload BEFORE the dense Option Chain table
  // reads it, so the chain never flashes back to the primary expiry on a
  // live tick. This is the same helper renderDashboard() uses for the rest
  // of the dashboard (see applyExpirySelection below), so both the KPI/
  // analytics panels and the Option Chain table stay driven by one piece of
  // state (_selectedExpiry) off one payload.
  applyExpirySelection(_wsState, _selectedExpiry);

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
  if(_wsState.spot != null) priceChart.addTick(_wsState.spot, Date.now(), null);

  // Drive the native Option Chain table/right panel straight off this same
  // tick — no separate WebSocket, no postMessage relay, no iframe.
  // refreshView() itself no-ops (checks for #tbody) when this page doesn't
  // have the dense chain markup mounted, so this is always safe to call
  // unconditionally.
  app.chainDense.refreshView(_wsState);

  // Multiple WS messages (e.g. spot+oi+greeks) often arrive back-to-back
  // for the same logical tick. Coalesce them into a single render per
  // animation frame instead of doing a full rebuild for each one.
  scheduleRender();
}

  scheduleRender(){
  if(_renderScheduled) return;
  _renderScheduled = true;
  requestAnimationFrame(function(){
    _renderScheduled = false;
    if(!_wsState) return;
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
    // applyExpirySelection off the fresh _wsState), but the Decision Engine
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
    const symbolChanged = _wsState.symbol && _lastRenderedSymbol && _wsState.symbol !== _lastRenderedSymbol;
    if (notYetBuilt || symbolChanged) {
      _lastRenderedSymbol = _wsState.symbol || _lastRenderedSymbol;
      parseAndRender(JSON.stringify(_wsState));
      // parseAndRender() just replaced #dashboard's entire innerHTML, which
      // wipes out the price-chart panel since it lives inside #dashboard —
      // remount it into the freshly-rebuilt container right here.
      if (window.priceChart) priceChart.render();
      if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(_wsState);
      return;
    }
    _lastRenderedSymbol = _wsState.symbol || _lastRenderedSymbol;
    _data = _wsState;
    if (window._afterRenderStratPayoff) _afterRenderStratPayoff();
    if (window._rerenderChainPanels) app.chain._rerenderChainPanels();
    // Decision Engine box + top-bar spot/badge/DTE strip are cheap to patch
    // in place too, so they stay live without rebuilding their containers.
    if (window.patchTopBarAndDecision) patchTopBarAndDecision(_wsState);
    // Live price chart — cheap redraw onto the existing canvas surface
    // (sizeCanvasIfChanged only resets it when the on-screen size changed),
    // so this rides the same per-frame coalescing as the other panels above.
    if (window.priceChart) priceChart.render();
    // Paper trading panel — lives outside #dashboard (self-mounted, see
    // bottom of file), so it's never touched by the rebuild above and just
    // needs its own cheap patch-in-place call here, same pattern as the
    // other panels on this line.
    if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(_wsState);
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

// ============================================================
// PORTED FROM option-chain.js (dense option-chain table page).
// Only the parts with no equivalent in dashboard.js were brought over:
//  - Renamed ChainView -> ChainDenseView (dashboard.js already has its
//    own ChainView for the main dashboard's chain-body rendering; same
//    class name would've thrown "Identifier already declared").
//  - Dropped option-chain.js's own DataService/WebSocket client — this
//    page now rides dashboard.js's existing WS connection instead of
//    opening a second socket to the same backend. refreshView() is
//    called directly from updateDashboard() below on every tick.
//  - Dropped sideBias()/combinedSignal() (buggy: it scored CE writing as
//    bullish using the PE rule for both sides). Replaced with
//    chainCombinedSignal(), which reuses dashboard.js's already-correct
//    ceBias()/peBias() but keeps emitting this page's own sig-* CSS
//    classes (dashboard's combinedSignal() emits sp-* classes, which are
//    a different, incompatible badge style in styles.css).
//  - fmt() -> thin wrapper over dashboard.js's fmtK() that preserves
//    fmt()'s "—" for null (fmtK() alone would render "0").
//  - Expiry selection: this view no longer keeps its own expiry state.
//    The global #expirySelect dropdown (in the .head bar) is the single
//    expiry control for the whole app; its onchange calls onExpiryChange()
//    (ChainView), which mutates the shared payload and then calls
//    app.chainDense.refreshView() directly so this table stays in sync.
//    On every WebSocket tick, updateDashboard() runs the same shared
//    applyExpirySelection(payload, _selectedExpiry) helper before handing
//    the payload to refreshView() here, so a live tick can never revert
//    the table back to the primary/default expiry.
// ============================================================

function fmt(n){ return n==null ? '—' : fmtK(n); }
function sign(n){ return n==null ? '—' : (n>0?'+':'') + n; }
function dirClass(n){ return n>0?'up':n<0?'down':'flat'; }
// Finds the strike where dealer net GEX crosses zero (short γ -> long γ or
// vice versa). Was previously `arr.find((g,i)=>i>0&&Math.sign(g.netGEX)!==
// Math.sign(arr[i-1].netGEX))` — but the full strike list (n_strikes_each_
// side defaults to 999 in engine.py) includes plenty of deep OTM/ITM
// strikes with zero OI on both legs, where netGEX is exactly 0.
// Math.sign(0) is 0, which is neither 1 nor -1, so the very first boundary
// between "no OI, netGEX===0" and "any real OI at all" got flagged as a
// sign change — producing a "flip strike" far from spot that had nothing
// to do with an actual short/long gamma crossover. Skipping near-zero
// (no real exposure) strikes fixes that.
const GEX_FLIP_EPS = 1e-6;
function findGammaFlipStrike(arr){
  if (!arr || !arr.length) return null;
  let prevSign = null;
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i].netGEX || 0;
    if (Math.abs(v) < GEX_FLIP_EPS) continue; // no real exposure at this strike — skip, don't compare
    const s = Math.sign(v);
    if (prevSign !== null && s !== prevSign) return arr[i];
    prevSign = s;
  }
  return null;
}
function cell(primary, secondary, primClass, secClass){
  return `<div class="cell"><span class="p ${primClass||''}">${primary}</span><span class="s ${secClass||''}">${secondary}</span></div>`;
}
function chainCombinedSignal(ceSignal, peSignal){
  const cb = ceBias(ceSignal), pb = peBias(peSignal);
  const sum = cb + pb;
  if (cb > 0 && pb > 0) return { label: 'Strong Bullish', cls: 'sig-strongbull' };
  if (cb < 0 && pb < 0) return { label: 'Strong Bearish', cls: 'sig-strongbear' };
  if (cb !== 0 && pb !== 0) return { label: 'Mixed', cls: 'sig-mixed' };
  if (sum > 0) return { label: 'Bullish', cls: 'sig-bull' };
  if (sum < 0) return { label: 'Bearish', cls: 'sig-bear' };
  return { label: 'Neutral', cls: 'sig-n' };
}

class App {
  constructor() {
    this.ui = new UiControls();
    this.data = new DataService();
    this.chain = new ChainView();
    this.oiFlow = new OiFlowView();
    this.exec = new ExecView();
    this.strategy = new StrategyView();
    this.simulator = new SimulatorView();
    this.modal = new ModalManager();
    this.chainDense = new ChainDenseView();      // ported from option-chain.js
    this.chainRightPanel = new RightPanelView();  // ported from option-chain.js
  }
}

const app = new App();

// ── Legacy global compatibility shims ──
// The dashboard's inline HTML (onclick="...") and the functions below still
// call these by their original bare names. The shims transparently forward
// reads/writes/calls to the new class instances so none of that legacy
// markup or cross-function code had to change.
window.updateStickyOffsets = (...args) => app.ui.updateStickyOffsets(...args);
window.switchTimer = (...args) => app.ui.switchTimer(...args);
window.toggleControlSidebar = (...args) => app.ui.toggleControlSidebar(...args);
window.secJump = (...args) => app.ui.secJump(...args);
window.connectWebSocket = (...args) => app.data.connectWebSocket(...args);
window.updateDashboard = (...args) => app.data.updateDashboard(...args);
window.scheduleRender = (...args) => app.data.scheduleRender(...args);
window.triggerFile = (...args) => app.data.triggerFile(...args);
window.handleFile = (...args) => app.data.handleFile(...args);
window.triggerPaste = (...args) => app.data.triggerPaste(...args);
window.loadFromPaste = (...args) => app.data.loadFromPaste(...args);
window._readAndRender = (...args) => app.data._readAndRender(...args);
window.refreshDashboardFromExport = (...args) => app.data.refreshDashboardFromExport(...args);
window.startAutoRefresh = (...args) => app.data.startAutoRefresh(...args);
window.parseAndRender = (...args) => app.data.parseAndRender(...args);
window.doStickyRefresh = (...args) => app.data.doStickyRefresh(...args);
window.renderDashboard = (...args) => app.chain.renderDashboard(...args);
window.updateStickyNav = (...args) => app.chain.updateStickyNav(...args);
window.toggleGreeks = (...args) => app.chain.toggleGreeks(...args);
window.toggleGreekRow = (...args) => app.chain.toggleGreekRow(...args);
window.switchChainRange = (...args) => app.chain.switchChainRange(...args);
window.switchVelTab = (...args) => app.chain.switchVelTab(...args);
window.sizeAndScrollChain = (...args) => app.chain.sizeAndScrollChain(...args);
window.renderVelocity = (...args) => app.chain.renderVelocity(...args);
window.switchGrkTab = (...args) => app.chain.switchGrkTab(...args);
window.renderGreeksGex = (...args) => app.chain.renderGreeksGex(...args);
window.onExpiryChange = (...args) => app.chain.onExpiryChange(...args);
window._rerenderChainPanels = (...args) => app.chain._rerenderChainPanels(...args);
window.patchTopBarAndDecision = (...args) => app.chain.patchTopBarAndDecision(...args);
window.buildOiTopMoversStrip = (...args) => app.oiFlow.buildOiTopMoversStrip(...args);
window.buildOiFlowRows = (...args) => app.oiFlow.buildOiFlowRows(...args);
window.switchOiFlowTab = (...args) => app.oiFlow.switchOiFlowTab(...args);
window.renderExecutiveDashboard = (...args) => app.exec.renderExecutiveDashboard(...args);
window.buildDriversDraggersCard = (...args) => app.exec.buildDriversDraggersCard(...args);
window.buildFiiDiiCard = (...args) => app.exec.buildFiiDiiCard(...args);
window.progress = (...args) => app.exec.progress(...args);
window.signal = (...args) => app.exec.signal(...args);
window.renderStratPayoff = (...args) => app.strategy.renderStratPayoff(...args);
window._afterRenderStratPayoff = (...args) => app.strategy._afterRenderStratPayoff(...args);
window._populateStrikeDropdown = (...args) => app.strategy._populateStrikeDropdown(...args);
window.simInit = (...args) => app.simulator.simInit(...args);
window.simUpdate = (...args) => app.simulator.simUpdate(...args);
window.simRenderGEXChart = (...args) => app.simulator.simRenderGEXChart(...args);
window.simRenderVolGrid = (...args) => app.simulator.simRenderVolGrid(...args);
window.simRenderTable = (...args) => app.simulator.simRenderTable(...args);
window.openOIDashboardModal = (...args) => app.modal.openOIDashboardModal(...args);
window.closeOIDashboardModal = (...args) => app.modal.closeOIDashboardModal(...args);
window._oiEscHandler = (...args) => app.modal._oiEscHandler(...args);
window._openOIDashboardPopupFallback = (...args) => app.modal._openOIDashboardPopupFallback(...args);

// ── Ported from option-chain.js — dense chain page shims ──
window.setStatus = (...args) => app.chainDense.setStatus(...args);
window.mapPayloadToRows = (...args) => app.chainDense.mapPayloadToRows(...args);
window.buildRowsHtml = (...args) => app.chainDense.buildRowsHtml(...args);
window.filterRowsByRange = (...args) => app.chainDense.filterRowsByRange(...args);
window.tickFill = (...args) => app.chainDense.tickFill(...args);
window.buildVelocityLookup = (...args) => app.chainDense.buildVelocityLookup(...args);
window.updateHeader = (...args) => app.chainDense.updateHeader(...args);
window.renderExpiryOptions = (...args) => app.chainDense.renderExpiryOptions(...args);
window.selectDepthStrike = (...args) => app.chainDense.selectDepthStrike(...args);
window.refreshView = (...args) => app.chainDense.refreshView(...args);
window.renderRightPanel = (...args) => app.chainRightPanel.renderRightPanel(...args);
window.buildDepthBoxHtml = (...args) => app.chainRightPanel.buildDepthBoxHtml(...args);
window.rpBar = (...args) => app.chainRightPanel.rpBar(...args);

Object.defineProperty(window, 'currentRange', {
  configurable: true,
  get() { return app.chainDense.currentRange; },
  set(v) { app.chainDense.currentRange = v; },
});
Object.defineProperty(window, 'velocityWindowMin', {
  configurable: true,
  get() { return app.chainDense.velocityWindowMin; },
  set(v) { app.chainDense.velocityWindowMin = v; },
});
Object.defineProperty(window, 'prevSnapshot', {
  configurable: true,
  get() { return app.chainDense.prevSnapshot; },
  set(v) { app.chainDense.prevSnapshot = v; },
});
Object.defineProperty(window, 'selectedDepthStrike', {
  configurable: true,
  get() { return app.chainRightPanel.selectedDepthStrike; },
  set(v) { app.chainRightPanel.selectedDepthStrike = v; },
});

Object.defineProperty(window, '_fsaHandle', {
  configurable: true,
  get() { return app.data.fsaHandle; },
  set(v) { app.data.fsaHandle = v; },
});
Object.defineProperty(window, '_legacyFile', {
  configurable: true,
  get() { return app.data.legacyFile; },
  set(v) { app.data.legacyFile = v; },
});
Object.defineProperty(window, '_data', {
  configurable: true,
  get() { return app.data.data; },
  set(v) { app.data.data = v; },
});
Object.defineProperty(window, '_autoRefreshTimer', {
  configurable: true,
  get() { return app.data.autoRefreshTimer; },
  set(v) { app.data.autoRefreshTimer = v; },
});
Object.defineProperty(window, '_countdownTimer', {
  configurable: true,
  get() { return app.data.countdownTimer; },
  set(v) { app.data.countdownTimer = v; },
});
Object.defineProperty(window, '_timerMins', {
  configurable: true,
  get() { return app.data.timerMins; },
  set(v) { app.data.timerMins = v; },
});
Object.defineProperty(window, '_renderScheduled', {
  configurable: true,
  get() { return app.data.renderScheduled; },
  set(v) { app.data.renderScheduled = v; },
});
Object.defineProperty(window, '_lastRenderedSymbol', {
  configurable: true,
  get() { return app.data.lastRenderedSymbol; },
  set(v) { app.data.lastRenderedSymbol = v; },
});
Object.defineProperty(window, '_ws', {
  configurable: true,
  get() { return app.data.wsManager.ws; },
  set(v) { app.data.wsManager.ws = v; },
});
Object.defineProperty(window, '_wsState', {
  configurable: true,
  get() { return app.data.wsManager.state; },
  set(v) { app.data.wsManager.state = v; },
});
Object.defineProperty(window, '_wsUrl', {
  configurable: true,
  get() { return app.data.wsManager.url; },
  set(v) { app.data.wsManager.url = v; },
});
Object.defineProperty(window, '_wsReconnectTimer', {
  configurable: true,
  get() { return app.data.wsManager.reconnectTimer; },
  set(v) { app.data.wsManager.reconnectTimer = v; },
});
Object.defineProperty(window, '_velWin', {
  configurable: true,
  get() { return app.chain.velWin; },
  set(v) { app.chain.velWin = v; },
});
Object.defineProperty(window, '_centerChainOnATM', {
  configurable: true,
  get() { return app.chain.centerChainOnATM; },
  set(v) { app.chain.centerChainOnATM = v; },
});
Object.defineProperty(window, '_grkView', {
  configurable: true,
  get() { return app.chain.grkView; },
  set(v) { app.chain.grkView = v; },
});
Object.defineProperty(window, '_chainRange', {
  configurable: true,
  get() { return app.chain.chainRange; },
  set(v) { app.chain.chainRange = v; },
});
Object.defineProperty(window, '_greeksVisible', {
  configurable: true,
  get() { return app.chain.greeksVisible; },
  set(v) { app.chain.greeksVisible = v; },
});
Object.defineProperty(window, '_pcrVisible', {
  configurable: true,
  get() { return app.chain.pcrVisible; },
  set(v) { app.chain.pcrVisible = v; },
});
Object.defineProperty(window, '_selStrike', {
  configurable: true,
  get() { return app.chain.selStrike; },
  set(v) { app.chain.selStrike = v; },
});
Object.defineProperty(window, '_selectedExpiry', {
  configurable: true,
  get() { return app.chain.selectedExpiry; },
  set(v) { app.chain.selectedExpiry = v; },
});
Object.defineProperty(window, '_expiryViewCache', {
  configurable: true,
  get() { return app.chain.expiryViewCache; },
  set(v) { app.chain.expiryViewCache = v; },
});
Object.defineProperty(window, '_oiFlowMode', {
  configurable: true,
  get() { return app.oiFlow.oiFlowMode; },
  set(v) { app.oiFlow.oiFlowMode = v; },
});
Object.defineProperty(window, '_selStratIdx', {
  configurable: true,
  get() { return app.strategy.selStratIdx; },
  set(v) { app.strategy.selStratIdx = v; },
});
Object.defineProperty(window, '_simSpotOverride', {
  configurable: true,
  get() { return app.simulator.simSpotOverride; },
  set(v) { app.simulator.simSpotOverride = v; },
});
Object.defineProperty(window, '_simIvOverride', {
  configurable: true,
  get() { return app.simulator.simIvOverride; },
  set(v) { app.simulator.simIvOverride = v; },
});
Object.defineProperty(window, '_simVelOverride', {
  configurable: true,
  get() { return app.simulator.simVelOverride; },
  set(v) { app.simulator.simVelOverride = v; },
});
Object.defineProperty(window, '_simDealerOverride', {
  configurable: true,
  get() { return app.simulator.simDealerOverride; },
  set(v) { app.simulator.simDealerOverride = v; },
});
Object.defineProperty(window, '_simState', {
  configurable: true,
  get() { return app.simulator.simState; },
  set(v) { app.simulator.simState = v; },
});
Object.defineProperty(window, '_oiDashboardWin', {
  configurable: true,
  get() { return app.modal.oiDashboardWin; },
  set(v) { app.modal.oiDashboardWin = v; },
});
Object.defineProperty(window, '_oiFrameLoaded', {
  configurable: true,
  get() { return app.modal.oiFrameLoaded; },
  set(v) { app.modal.oiFrameLoaded = v; },
});



// ── STICKY HEADER STACK ──
// .sec-nav (#sec-nav-bar) and .top-bar (#sec-topbar) are stacked sticky
// elements, followed by .chain-right-panel further down. Their `top`
// offsets used to be hand-picked pixel guesses (62px / 116px) baked into
// the CSS, which silently drift out of sync whenever either bar's real
// height changes — e.g. the VIX pill wrapping the top-bar to two lines,
// or the risk/strategy nav buttons toggling visible — causing the bar
// below to overlap or leave a gap. This measures the actual rendered
// heights and feeds them back in as CSS custom properties so the offsets
// stay correct no matter what's currently showing.
const STICKY_GAP = 6; // matches .sec-nav / .top-bar margin-bottom
const STICKY_BASE_TOP = 8; // matches .sec-nav's own `top`

window.addEventListener('load', updateStickyOffsets);
window.addEventListener('resize', updateStickyOffsets);

// When true, the next chain render centers the viewport on the ATM strike
// (initial load, or right after the user changes the ATM range). On
// ordinary live-data re-renders this stays false so a manual scroll
// position isn't yanked back to ATM mid-read.

   // remembers user's Strategy Payoff dropdown choice across re-renders
  // remembers user's strike dropdown choice across re-renders (null = ATM)
 // remembers the expiry dropdown across live JSON refreshes
 // keeps selected expiry visible if a live tick omits that chain
 // simulator slider overrides — null = use live default

// ── WEBSOCKET LIVE FEED (replaces JSON file polling) ──

          // accumulated full dashboard state

// ── INDEX TICKER STRIP (NIFTY / BANKNIFTY / MIDCPNIFTY / SENSEX) ──
// Fixed left-to-right order — NIFTY always first, regardless of which
// symbol the dashboard is currently connected to. Only .idx-pill.active
// (a highlight ring) reflects selection state; position never does.
const INDEX_TICKER_ORDER = ['NIFTY','BANKNIFTY','MIDCPNIFTY','SENSEX'];

// Reads live quotes from d.indexQuotes = { NIFTY:{spot,spotChange,spotChgPct}, ... }
// pushed by ws_server_live.py's index_quote_loop() (see INDEX_QUOTES there —
// key names must match exactly, this was previously reading a `chgPct`
// field that the backend never sends, so every non-active pill silently
// showed 0.00% forever instead of the real change).
// The active symbol is deliberately left OUT of this strip — its spot/
// change is already the big readout immediately to the left, so repeating
// it as a same-size pill here was pure duplication. A VIX pill takes that
// same first slot instead (relocated from the old expiry-strip VIX pill),
// which is more useful screen space than a second copy of the number
// already showing. The remaining (non-active) indices show a "—"
// placeholder for % change until indexQuotes is wired up on the backend.
// In your dashboard.js — Updated render function
function renderIndexTicker(d) {
  if (!d) d = {};

  // The 'indices' array comes from your new unified stream in mTerminals_json.py
  const indices = d.allIndices || [];
  const active = d.symbol || 'NIFTY';

  // VIX Logic remains the same, assuming d.indiaVix is still passed.
  // %change badge — reads d.indiaVixChgPct, which mTerminals_json.py sends
  // as ctx_dict["india_vix_chg_pct"] (defaults to 0.0 upstream if that
  // context field isn't populated yet — so a real 0 and "not wired up"
  // will look identical until india_vix_chg_pct is actually computed).
  const vixRegime = (d.vixRegime || '').toLowerCase();
  const vixColor = vixRegime === 'high' ? '#FF6B6B' : vixRegime === 'low' ? '#20C997' : '#FFD43B';
  const vixUp = (d.indiaVixChgPct || 0) >= 0;
  const vixChgHtml = d.indiaVixChgPct !== undefined
    ? `<span class="idx-pill-chg ${vixUp ? 'up' : 'down'}">${vixUp ? '▲' : '▼'}${Math.abs(d.indiaVixChgPct).toFixed(2)}%</span>`
    : '';
  const vixPill = `<div class="idx-pill idx-pill-vix" title="India VIX">
    <span class="idx-pill-sym">VIX</span>
    <span class="idx-pill-val" style="color:${vixColor};">${fmtN(d.indiaVix, 1)}</span>
    ${vixChgHtml}
  </div>`;

  // Map display names to backend symbols (matches market_api.py INDEX_RENAME)
  // Backend now sends renamed symbols (NIFTY, BANKNIFTY) directly
  const symbolMap = {
    'NIFTY': 'NIFTY',
    'NIFTY BANKNIFTY': 'BANKNIFTY',
    'FINNIFTY': 'FINNIFTY',
    'MIDCPNIFTY': 'MIDCPNIFTY',
    'SENSEX': 'SENSEX'
  };

  // Map the new unified index list directly to pills
  const pills = indices
    .filter(idx => (idx.BackendSymbol || idx.Symbol) !== active) // Match your rename_index mapping
    .map(idx => {
      const pChange = parseFloat(idx["% Change"]) || 0;
      const up = pChange >= 0;
      const backendSymbol = idx.BackendSymbol || idx.Symbol;
      const displayName = idx.Symbol;

      return `<div class="idx-pill" onclick="switchActiveIndex('${backendSymbol}')" title="Switch to ${displayName}">
        <span class="idx-pill-sym">${displayName}</span>
        <span class="idx-pill-val">${fmtI(idx["Last Price"])}</span>
        <span class="idx-pill-chg ${up ? 'up' : 'down'}">${up ? '▲' : '▼'}${Math.abs(pChange).toFixed(2)}%</span>
      </div>`;
    }).join('');

  return `<div class="index-ticker" id="index-ticker-bar">${vixPill}${pills}</div>`;
}
// ── EXPIRY SELECT RE-PARENTING ──
// #expirySelect lives once in the static DOM (see #expiry-select-holder in
// DashboardPro.html) and is never rebuilt from an HTML string — every
// top-bar redraw (full rebuild or the lighter per-tick patch) creates a
// fresh #expiry-slot placeholder (its own dedicated pill, separate from
// DTE), and this just moves the *same* <select> node into it.
//
// BUG THIS FIXES: looking the select up via document.getElementById on
// every call breaks after the first move. Once the select is appended
// into the top-bar's #expiry-slot, the *next* outerHTML replacement of
// #sec-topbar (patchTopBarAndDecision / renderDashboard) destroys that
// whole subtree — including the slot the select was sitting in — which
// detaches the select from the live document entirely. A detached node is
// invisible to getElementById, so every call after the first silently
// found nothing and the dropdown vanished for good. Caching the node
// reference once (the first time it's found) means we keep working with
// the actual same object even after it's been detached, and can always
// re-attach it into whatever fresh #expiry-slot shows up next.
let _expirySelectNode = null;
function getExpirySelectNode(){
  if(!_expirySelectNode) _expirySelectNode = document.getElementById('expirySelect');
  return _expirySelectNode;
}
function moveExpirySelectIntoTopBar(){
  const sel = getExpirySelectNode();
  const slot = document.getElementById('expiry-slot');
  if(sel && slot && sel.parentNode !== slot) slot.appendChild(sel);
}
window.moveExpirySelectIntoTopBar = moveExpirySelectIntoTopBar;

// Click handler for the pills. Switching the active index means
// reconnecting to that symbol's engine — how that's routed depends on the
// backend (a `?symbol=` query param the server reads, a distinct port per
// symbol via ws_server_live.py --symbol, etc). Define
// window.onIndexSwitchRequested(sym) before this script runs to wire the
// real routing; absent that, this falls back to a `?symbol=` query param
// on the current WS URL as the simplest single-port convention.
// ── TOP-BAR SYMBOL PICKER ──
// Seed/fallback list shown until /api/symbols resolves (see fetchSymbolList()
// in the DOMContentLoaded block below) — kept as a small known-good set in
// case that fetch is slow or fails, not a whitelist. Once /api/symbols
// returns, its contents (every OPTIDX/OPTSTK `name` in the ScripMaster —
// same primary key find_option_token()/list_expiries() key off) replace
// these in place. Mutated via length=0+push rather than reassigned, so
// chain-views.js's reference to this same array stays live.
const COMMON_SYMBOLS = ['NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY','SENSEX','BANKEX'];

// Fetches the full underlying list from the backend (backed by
// smartapi_client.list_underlyings()) and swaps it into COMMON_SYMBOLS in
// place. Fire-and-forget — called once from the DOMContentLoaded bootstrap;
// if it fails or is slow, the picker just keeps showing the seed list above
// until the next render happens to catch the updated array.
async function fetchSymbolList(){
  try{
    const res = await fetch('/api/symbols');
    if(!res.ok) return;
    const list = await res.json();
    if(Array.isArray(list) && list.length){
      COMMON_SYMBOLS.length = 0;
      COMMON_SYMBOLS.push(...list);
    }
  }catch(e){
    console.warn('[symbols] /api/symbols fetch failed, keeping seed list', e);
  }
}
window.fetchSymbolList = fetchSymbolList;

// Called by the top-bar <select onchange>. "Other…" prompts for a free-
// text symbol (individual stocks, etc.) instead of switching straight
// away — picking it directly as a value would just try to load a symbol
// literally named "__other__".
function onSymbolPicked(val){
  if(val === '__other__'){
    const sym = prompt('Symbol to switch to (e.g. RELIANCE):');
    if(sym) switchActiveIndex(sym.trim().toUpperCase());
    return;
  }
  switchActiveIndex(val);
}
window.onSymbolPicked = onSymbolPicked;

// Inside dashboard.js, within the DataService or global scope:
function switchActiveIndex(sym) {
  if (!sym) return;
  // If you need to hit a specific API before connecting:
  // fetch(`http://api/set_index?symbol=${sym}`).then(...)
  
  // Default behavior
  const base = (_wsUrl || '').split('?')[0];
  connectWebSocket(`${base}?symbol=${encodeURIComponent(sym)}`);
}
window.switchActiveIndex = switchActiveIndex;
window.renderIndexTicker = renderIndexTicker;

// deepMerge() and applyDelta() moved to ws-manager.js along with WSManager,
// the only caller of either. See that file for both implementations.

// Called for every inbound WS message.
// msg = { type: "full" | "spot" | "oi" | "greeks" | "alerts" | "iv" | "decision", payload: {...} }
// "full" replaces the whole state; any other type is merged into the
// matching slice of state, then the dashboard is re-rendered from the
// merged state. renderDashboard() is a pure function of state -> DOM,
// so this produces correct "only the affected component visibly
// changes" behavior without needing separate per-widget DOM patchers.

// ── SWITCH TIMER ──

// ── FILE HANDLING ──

// ── AUTO REFRESH ──

// ── MOJIBAKE REPAIR ──
// If the backend ever double-encodes text (e.g. a UTF-8 string read/written
// as Windows-1252 somewhere upstream), special characters like ₹, —, or ×
// show up as garbled sequences such as "â‚¹", "â€”", "Ã—". This detects that
// specific, well-known corruption pattern and reverses it. It's a no-op
// (returns the original string untouched) for anything that isn't actually
// mojibake, so it's safe to run on every string from the feed.
// Windows-1252 remaps bytes 0x80-0x9F to non-Latin-1 codepoints (€, —, smart
// quotes, etc.) — that 0x80-0x9F range is exactly where ₹/—/× land, so a
// plain "codepoint & 0xFF" byte reconstruction silently mangles them. This
// table maps those codepoints back to their original byte value.
const _CP1252_REV = {0x20AC:0x80,0x201A:0x82,0x0192:0x83,0x201E:0x84,0x2026:0x85,0x2020:0x86,0x2021:0x87,
  0x02C6:0x88,0x2030:0x89,0x0160:0x8A,0x2039:0x8B,0x0152:0x8C,0x017D:0x8E,0x2018:0x91,0x2019:0x92,
  0x201C:0x93,0x201D:0x94,0x2022:0x95,0x2013:0x96,0x2014:0x97,0x02DC:0x98,0x2122:0x99,0x0161:0x9A,
  0x203A:0x9B,0x0153:0x9C,0x017E:0x9E,0x0178:0x9F};
function _fixMojibake(s){
  if(typeof s!=='string' || !/[ÂÃâ]/.test(s)) return s;
  const bytes=[];
  for(const ch of s){
    const cp=ch.codePointAt(0);
    if(cp<=0xFF) bytes.push(cp);
    else if(_CP1252_REV.hasOwnProperty(cp)) bytes.push(_CP1252_REV[cp]);
    else return s; // a character here can't be a mis-decoded single byte — not mojibake, bail out
  }
  try{
    return new TextDecoder('utf-8',{fatal:true}).decode(new Uint8Array(bytes));
  }catch(e){
    return s; // not actually mojibake — leave as-is
  }
}
function _fixMojibakeDeep(obj, depth){
  if(depth===undefined) depth=0;
  if(depth>6 || obj==null) return obj;
  if(typeof obj==='string') return _fixMojibake(obj);
  if(Array.isArray(obj)){ for(let i=0;i<obj.length;i++) obj[i]=_fixMojibakeDeep(obj[i],depth+1); return obj; }
  if(typeof obj==='object'){ for(const k in obj) obj[k]=_fixMojibakeDeep(obj[k],depth+1); return obj; }
  return obj;
}

// ── PARSE & RENDER ──

// ── HELPERS ──
function fmtN(v,d){if(v==null||isNaN(v))return'—';return parseFloat(v).toFixed(d===undefined?2:d);}
function fmtK(v){v=parseFloat(v)||0;if(Math.abs(v)>=100000)return(v/100000).toFixed(2)+'L';if(Math.abs(v)>=1000)return(v/1000).toFixed(1)+'K';return Math.round(v).toString();}
function fmtI(v){return(parseFloat(v)||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});}
function sClr(v){return v>=0?'var(--green)':'var(--red)';}
function ceOiChgClr(v){return v>=0?'var(--red)':'var(--green)';}

function spClass(s){
  if(!s)return'sp-n';
  s=s.toLowerCase();
  if(s.includes('long build')||s.includes('buying')||s.includes('lb'))return'sp-lb';
  if(s.includes('short cover')||s.includes('covering')||s.includes('sc'))return'sp-sc';
  if(s.includes('short build')||s.includes('writing')||s.includes('unwind')||s.includes('sb'))return'sp-sb';
  return'sp-n';
}

function ceBias(s){
  if(!s)return 0;
  s=s.toLowerCase();
  if(s.includes('writing')||s.includes('short build'))return -1;
  if(s.includes('unwind')||s.includes('cover'))return 1;
  return 0;
}

function peBias(s){
  if(!s)return 0;
  s=s.toLowerCase();
  if(s.includes('writing')||s.includes('short build')||s.includes('buying')||s.includes('long build'))return 1;
  if(s.includes('unwind')||s.includes('cover'))return -1;
  return 0;
}

function combinedSignal(ceSignal,peSignal){
  const cb=ceBias(ceSignal),pb=peBias(peSignal);
  const sum=cb+pb;
  if(cb>0&&pb>0)return{label:'Strong Bullish',cls:'sp-strongbull'};
  if(cb<0&&pb<0)return{label:'Strong Bearish',cls:'sp-strongbear'};
  if(cb!==0&&pb!==0)return{label:'Mixed',cls:'sp-mixed'};
  if(sum>0)return{label:'Bullish',cls:'sp-bull'};
  if(sum<0)return{label:'Bearish',cls:'sp-bear'};
  return{label:'Neutral',cls:'sp-n'};
}

function biasCls(b){
  if(!b)return'badge-neutral';
  b=b.toLowerCase();
  if(b.includes('bull'))return'badge-bull';
  if(b.includes('bear'))return'badge-bear';
  return'badge-neutral';
}

function pcrCls(p){return p>1.3?'badge-bull':p<0.8?'badge-bear':'badge-neutral';}

function activeAtm(d){
  if(!d) return 0;
  const chain=(d.chain)||[];
  if(d.atm && chain.some(r=>r.strike===d.atm)) return d.atm;
  const atmRow=chain.find(r=>r.atm);
  if(atmRow) return atmRow.strike;
  const rowWithAtmStrike=chain.find(r=>r.atmStrike && chain.some(x=>x.strike===r.atmStrike));
  if(rowWithAtmStrike) return rowWithAtmStrike.atmStrike;
  const spot=parseFloat(d.spot)||0;
  if(spot && chain.length) return chain.reduce((best,r)=>Math.abs(r.strike-spot)<Math.abs(best.strike-spot)?r:best,chain[0]).strike;
  return d.atm||0;
}

function applyExpirySelection(d, selectedExpiry){
  if(!d) return;
  d._primaryExpiry = d._primaryExpiry || d.expiry || '';
  d._activeExpiry = selectedExpiry || d._primaryExpiry;
  if(!selectedExpiry || selectedExpiry === d._primaryExpiry){
    // Switching back to the primary expiry (or first render). Restore the
    // primary values that got overwritten below the last time a non-primary
    // expiry was selected — these _primary* backups were being written but
    // never read back, so d.chain/d.atm/d.oiVelocity/etc used to stay
    // stuck on the last-selected expiry's data even after switching back.
    if(d._primaryChain      !== undefined) d.chain      = d._primaryChain;
    if(d._primaryGreeks     !== undefined) d.greeks     = d._primaryGreeks;
    if(d._primaryAtm        !== undefined) d.atm        = d._primaryAtm;
    if(d._primaryDte        !== undefined) d.dte        = d._primaryDte;
    if(d._primaryCeWall     !== undefined) d.ceWall     = d._primaryCeWall;
    if(d._primaryPeWall     !== undefined) d.peWall     = d._primaryPeWall;
    if(d._primaryMaxPain    !== undefined) d.maxPain    = d._primaryMaxPain;
    if(d._primaryPCR        !== undefined) d.totalPCR   = d._primaryPCR;
    if(d._primaryCallPremium!== undefined) d.callPremium= d._primaryCallPremium;
    if(d._primaryPutPremium !== undefined) d.putPremium = d._primaryPutPremium;
    if(d._primaryAtmIV      !== undefined) d.atmIV      = d._primaryAtmIV;
    if(d._primaryAtmDelta   !== undefined) d.atmDelta   = d._primaryAtmDelta;
    if(d._primaryAtmGamma   !== undefined) d.atmGamma   = d._primaryAtmGamma;
    if(d._primaryAtmTheta   !== undefined) d.atmTheta   = d._primaryAtmTheta;
    if(d._primaryAtmVega    !== undefined) d.atmVega    = d._primaryAtmVega;
    if(d._primaryOiVelocity !== undefined) d.oiVelocity = d._primaryOiVelocity;
    return;
  }

  const chainStore = d.chains || {};
  const metaStore = d.chainMeta || {};
  const cached = _expiryViewCache[selectedExpiry] || {};
  const selectedChainSrc = chainStore[selectedExpiry] || cached.chain;
  if(!selectedChainSrc || !selectedChainSrc.length) return;
  const selectedMeta = metaStore[selectedExpiry] || cached.meta || {};
  // IMPORTANT: never hand out the same array/row objects that live in
  // d.chains[selectedExpiry] / _expiryViewCache. d.chain gets mutated
  // in place by applyDelta() on every live WS tick (Object.assign on
  // matching-strike rows) — deltas only ever carry the primary/near
  // expiry's ticks, so if d.chain aliased the cached array, those
  // primary-expiry field patches would silently bleed into this
  // expiry's cached rows (by matching strike number) and corrupt the
  // cache until the next 'full' resync. Clone so d.chain is a
  // disposable working copy every time.
  const selectedChain = selectedChainSrc.map(row => Object.assign({}, row));
  _expiryViewCache[selectedExpiry] = { chain: selectedChainSrc, meta: selectedMeta };

  // Only back up primary values the FIRST time we swap away from the
  // primary expiry in a given payload lifetime (the || guards) — repeated
  // ticks while a non-primary expiry stays selected must not clobber the
  // backup with the already-swapped-in values.
  d._primaryChain       = d._primaryChain       || d.chain;
  d._primaryGreeks       = d._primaryGreeks     || d.greeks;
  d._primaryAtm         = d._primaryAtm         || d.atm;
  d._primaryDte         = d._primaryDte         || d.dte;
  d._primaryCeWall      = d._primaryCeWall      || d.ceWall;
  d._primaryPeWall      = d._primaryPeWall      || d.peWall;
  d._primaryMaxPain     = d._primaryMaxPain     || d.maxPain;
  d._primaryPCR         = d._primaryPCR         || d.totalPCR;
  d._primaryCallPremium = d._primaryCallPremium || d.callPremium;
  d._primaryPutPremium  = d._primaryPutPremium  || d.putPremium;
  d._primaryAtmIV       = d._primaryAtmIV       || d.atmIV;
  d._primaryAtmDelta    = d._primaryAtmDelta    || d.atmDelta;
  d._primaryAtmGamma    = d._primaryAtmGamma    || d.atmGamma;
  d._primaryAtmTheta    = d._primaryAtmTheta    || d.atmTheta;
  d._primaryAtmVega     = d._primaryAtmVega     || d.atmVega;
  d._primaryOiVelocity  = d._primaryOiVelocity  || d.oiVelocity;

  d.chain = selectedChain;
  const meta = selectedMeta;
  d.expiry = selectedExpiry;
  if(meta.greeks      != null) d.greeks      = meta.greeks;
  if(meta.atm         != null) d.atm         = meta.atm;
  if(meta.dte         != null) d.dte         = meta.dte;
  if(meta.ceWall      != null) d.ceWall      = meta.ceWall;
  if(meta.peWall      != null) d.peWall      = meta.peWall;
  if(meta.maxPain     != null) d.maxPain     = meta.maxPain;
  if(meta.totalPCR    != null) d.totalPCR    = meta.totalPCR;
  if(meta.straddle    != null){ d.callPremium = meta.straddle/2; d.putPremium = meta.straddle/2; }
  if(meta.callPremium != null) d.callPremium = meta.callPremium;
  if(meta.putPremium  != null) d.putPremium  = meta.putPremium;
  if(meta.atmIV       != null) d.atmIV       = meta.atmIV;
  if(meta.atmDelta    != null) d.atmDelta    = meta.atmDelta;
  if(meta.atmGamma    != null) d.atmGamma    = meta.atmGamma;
  if(meta.atmTheta    != null) d.atmTheta    = meta.atmTheta;
  if(meta.atmVega     != null) d.atmVega     = meta.atmVega;
  // ── OI VELOCITY ──
  // d.oiVelocity was never swapped per expiry before this fix, so every
  // OI-Vel view (butterfly "OI Vel" tab, Greeks/GEX panel, right-panel
  // totals) kept showing the PRIMARY expiry's velocity numbers no matter
  // which expiry was selected. This requires the backend to actually send
  // per-expiry velocity data in chainMeta[expiry].oiVelocity — if it
  // doesn't, this falls back to leaving the primary expiry's velocity in
  // place (same as before) rather than showing wrong/blank data.
  if(meta.oiVelocity  != null) d.oiVelocity  = meta.oiVelocity;
  if(!d.atm) d.atm = activeAtm(d);
}

function getFilteredChain(d){
  const chainAll=(d&&d.chain)||[];
  if(_chainRange===9999) return chainAll;
  const atm=activeAtm(d);
  const idx=chainAll.findIndex(r=>r.atm||r.strike===atm);
  return idx<0?chainAll:chainAll.filter((r,i)=>Math.abs(i-idx)<=_chainRange);
}

function velMiniCell(v,maxAbs,clr){
  v=v||0;
  const pct=maxAbs>0?Math.max(Math.min(Math.abs(v)/maxAbs*24,24),2):2;
  return `<div class="vel-mini-wrap"><div class="vel-mini-bar" style="width:${pct.toFixed(0)}px;background:${clr};"></div><span class="vel-mini-val" style="color:${clr};">${v>=0?'+':''}${fmtK(v)}</span></div>`;
}

// Builds the strike-by-strike butterfly rows for the OI Flow chart.
// mode: 'oi' (open interest) | 'chg' (intraday OI change) | 'vel' (OI velocity, current _velWin)
function oiFlowLabel(mode){
  return mode==='chg'?'OI Chg':mode==='vel'?`OI Vel (${_velWin}m)`:'OI';
}
// Single-line highlight of the biggest CE build and biggest PE build in the
// visible strike range — the rest of that detail already lives in the
// butterfly chart itself (switch to the "OI Chg" tab), so this stays a
// 1-line callout rather than repeating the full ranked list.
// mode-aware: pulls the "biggest build" figure from whichever series is
// currently shown in the butterfly chart (OI / OI Chg / OI Vel), instead of
// always reading the raw intraday OI-change column.

// ── TOP 3 DRIVERS / DRAGGERS ──
// Expects d.contributors = [{ symbol, pointImpact (or point_impact),
// pctChange (or pct_change) }, ...] — one entry per index heavyweight,
// as produced by DraggerDriver.py / derive_top_contributors(). Renders
// a placeholder until the backend actually populates that field on the
// WS payload. Returned as the 3rd exec-card, sitting alongside Market
// Health / Market Story in the same exec-grid row.

// ── RENDER DASHBOARD ──

// ── UPDATE STICKY NAV ──

// ── TOGGLES ──

// ── SWITCH CHAIN RANGE ──
// Locate these functions in DashboardPro.html and add the postMessage dispatches:

// ── SIZE & SCROLL CHAIN VIEWPORT ──
// Caps the visible chain to ~5 strikes either side of ATM (11 data rows)
// no matter which ATM Range is selected, and either re-centers on ATM
// (initial load / range change) or restores the reader's previous scroll
// position (ordinary live-data re-renders).

// ── TOGGLE CONTROL SIDEBAR ──

// ── RENDER VELOCITY ──

// ── SWITCH GRK TAB ──

// ── RENDER GREEKS (left column: Delta/Gamma/Theta/Vega) ──

// ── RENDER GEX (right column) ──

// ── STICKY REFRESH ──

// ── EXPIRY CHANGE HANDLER ──
function _dteFromStr(dateStr){
  // Parse "DD-Mon-YYYY" → calendar days from today
  if(!dateStr) return 0;
  try{
    const months={Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
    const parts=dateStr.split('-');
    if(parts.length!==3) return 0;
    const d=parseInt(parts[0],10);
    const m=months[parts[1]];
    const y=parseInt(parts[2],10);
    if(m===undefined||isNaN(d)||isNaN(y)) return 0;
    const expDt=new Date(y,m,d);
    const today=new Date(); today.setHours(0,0,0,0);
    return Math.max(0,Math.round((expDt-today)/(1000*60*60*24)));
  }catch(e){return 0;}
}

// ── Re-render ALL chain-derived panels when expiry is switched ───────────────

// ── SECTION JUMP ──

// ══════════════════════════════════════════════════════
//  STRATEGY PAYOFF ENGINE
// ══════════════════════════════════════════════════════

// Compute per-strike P&L at expiry for one leg
function _legPnl(leg, underlyingAtExpiry, lotSize){
  const S   = underlyingAtExpiry;
  const K   = leg.strike;
  const ltp = parseFloat(leg.ltp) || 0;
  const lots= leg.lots || 1;
  const dir = leg.action === 'BUY' ? 1 : -1;
  const type= (leg.type||'').toUpperCase();
  let payoff= 0;
  if(type==='CE') payoff = Math.max(S - K, 0) - ltp;
  else if(type==='PE') payoff = Math.max(K - S, 0) - ltp;
  else if(type==='FUT') payoff = S - K - ltp;
  return dir * payoff * lots * lotSize;
}

// Populate strike dropdown from live chain data matching strategy legs

// Auto-trigger after data render

// Hook: call renderStratPayoff after renderDashboard populates the DOM

// PriceChartEngine moved to price-chart.js (loaded earlier in DashboardPro.html) — see that file's header comment.
// ── INIT ──
window.addEventListener('load', function(){
  const msg = document.getElementById('err-msg');
  if(msg) msg.textContent = '📂 Click "Open file" to load nifty_dashboard.json — auto-refresh every 5 min starts automatically.';
});

// ========================================================
// INSTITUTIONAL F&O SIMULATOR — Engine
// ========================================================

// Option Chain no longer has a modal/iframe — it's a native section (see
// .chain-layout in DashboardPro.html) driven by the same in-memory payload
// as the rest of the dashboard, refreshed via app.chainDense.refreshView().

// ── OI DASHBOARD MODAL (in-page, same screen/tab) ──
// Loads oi_dashboard.html into an iframe inside an overlay on top of this
// same page — no new browser window/tab is opened. Same file:// fallback
// caveat as the Option Chain modal above (see notes there).

// Fallback used only when running from file:// — see caveat above.

// Click on the dark backdrop (outside the panel) also closes it.
document.addEventListener('DOMContentLoaded', function(){
  var modal = document.getElementById('oi-dashboard-modal');
  if(modal){
    modal.addEventListener('click', function(e){
      if(e.target === modal) closeOIDashboardModal();
    });
  }
});

// ========================================================
// PAPER TRADING PANEL — self-mounted, no DashboardPro.html changes needed
// ========================================================
// Talks to paper_trading.py via new WS message types (need wiring into
// ws_server_live.py — see the "Suggested integration" note at the top of
// paper_trading.py):
//   OUTBOUND  {type:"place_order",  payload:{...}}  -> engine.place_order()
//   INBOUND   {type:"portfolio", payload:<get_portfolio_summary() dict>}
//   INBOUND   {type:"orders",    payload:<get_orders() list>}
// Both inbound types land in _wsState.portfolio / _wsState.orders for free
// via updateDashboard()'s generic deepMerge branch (top of file) — this
// panel only reads them and re-renders, it never touches _wsState itself.
// No existing WS send capability existed in this file before this block —
// sendWsMessage() below is the first one.


// ── AUTO-CONNECT ON LOAD ──
window.addEventListener('DOMContentLoaded', function(){
  const lbl = $i('ws-url-label');
  if(lbl) lbl.textContent = _wsUrl;
  priceChart.ensureMounted();
  // Backfill from the last few minutes of history before the WS starts
  // pushing live ticks. Fire-and-forget: connectWebSocket() doesn't wait
  // on it, and hydrateRange() itself no-ops safely if a live tick wins the
  // race or the endpoint errors (falls back to tick-bucketing for that
  // range, same as before this existed).
  priceChart.hydrateRange(priceChart.settings.range);
  connectWebSocket();
});

// ── DEFEAT BACK/FORWARD CACHE (bfcache) ──
// A normal refresh (Cmd+R) can, in some browsers, restore this page from
// bfcache instead of truly re-executing dashboard.js. That leaves the OLD
// WebSocket object (and _wsState) alive in memory, still pointed at
// whatever server process was running at the time the page was first
// loaded — so new data (or a restarted server on a different --symbol)
// never shows up until a hard reload bypasses bfcache entirely. Forcing
// a real reload whenever the page is restored from bfcache makes a plain
// refresh behave the same as Cmd+Shift+R for reconnect purposes.
window.addEventListener('pageshow', function(event){
  if (event.persisted) {
    location.reload();
  }
});