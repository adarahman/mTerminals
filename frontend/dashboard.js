// ============================================================
// dashboard.js
// Phase 1 bootstrap cleanup (see master optimization prompt, Task
// "Dashboard bootstrap cleanup"), extended by Phase 4 (Task "Introduce
// Panel Manager").
//
// This file is now an APPLICATION BOOTSTRAP ONLY: it creates the app's
// module instances, registers them with a PanelManager (panel-manager.js
// / dashboard-panels.js), exposes the legacy global compatibility shims
// that old inline onclick="..." markup and cross-module code call by
// their pre-refactor bare names, and registers the app's global (window/
// document) event listeners. It holds no DOM utilities, no UI-subsystem
// logic, no state/websocket logic, and no chain/expiry/index domain logic
// of its own anymore — all of that has been extracted into dedicated
// files, same treatment ws-manager.js/market-store.js/formatters.js
// already got:
//
//   dom-utils.js       — $i, err, setHtmlIfChanged, sizeCanvasIfChanged
//   ui-controls.js     — UiControls (sticky offsets, timer tabs, range/vel
//                        flyout, section-jump nav)
//   data-service.js    — DataService (WS/file/paste loading, auto-refresh,
//                        MarketStore/WSManager wiring, render scheduling)
//   chain-helpers.js   — shared chain/expiry/index pure functions
//                        (activeAtm, applyExpirySelection, getFilteredChain,
//                        ceBias/peBias/combinedSignal/biasCls/pcrCls,
//                        parseExpiryDate/sortExpiryDates, findGammaFlipStrike,
//                        velMiniCell, oiFlowLabel, renderIndexTicker,
//                        switchActiveIndex/onSymbolPicked/fetchSymbolList,
//                        moveExpirySelectIntoTopBar, mojibake repair, etc.)
//   formatters.js      — fmt/fmtN/fmtK/fmtI/sClr/ceOiChgClr/sign/dirClass/cell
//   panel-manager.js   — Panel base class + PanelManager registry
//                        (register/refresh/resize/destroy)
//   dashboard-panels.js — the six Panel subclasses (PriceChart,
//                        OptionChain, OiDashboard, PaperTrading,
//                        DecisionBox, MarketBreadth) registered below
//
// All of the above must load before this file — see DashboardPro.html
// script order.
// ============================================================

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
    this.chainDense = new ChainDenseView();      // slimmed: expiry options + BroadcastChannel sync to option-chain.html only

    // ── Panel Manager (Phase 4) ──
    // Registers the six panels named in the task against the view
    // instances constructed above. Each Panel subclass is a thin wrapper
    // (see dashboard-panels.js) — none of the classes above changed to
    // accommodate this; PanelManager just gives the app one registry to
    // refresh/resize/destroy through instead of the caller needing to
    // know which of app.chain/app.chainDense/app.oiFlow/app.modal/... to
    // reach for.
    this.panelManager = new PanelManager();
    this.panelManager.register(new PriceChartPanel());
    this.panelManager.register(new OptionChainPanel());
    this.panelManager.register(new OiDashboardPanel());
    this.panelManager.register(new PaperTradingPanel());
    this.panelManager.register(new DecisionBoxPanel());
    this.panelManager.register(new MarketBreadthPanel());
  }
}

const app = new App();
const panelManager = app.panelManager;
window.panelManager = panelManager;

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
// renderDashboard / _rerenderChainPanels / patchTopBarAndDecision now
// route through the OptionChain panel (dashboard-panels.js) instead of
// calling app.chain directly — same three granularities (full rebuild /
// expiry-switch incremental / per-tick top-bar+decision patch), same
// arguments, same effect; every existing bare-name call site (chain-
// view.js's switchChainRange/switchVelTab, data-service.js's render
// scheduling, etc.) keeps working unchanged.
window.renderDashboard = (...args) => panelManager.get('optionChain').refresh(...args);
window.updateStickyNav = (...args) => app.chain.updateStickyNav(...args);
window.switchChainRange = (...args) => app.chain.switchChainRange(...args);
window.switchVelTab = (...args) => app.chain.switchVelTab(...args);
window.renderVelocity = (...args) => app.chain.renderVelocity(...args);
window.switchGrkTab = (...args) => app.chain.switchGrkTab(...args);
window.renderGreeksGex = (...args) => app.chain.renderGreeksGex(...args);
window.onExpiryChange = (...args) => app.chain.onExpiryChange(...args);
window._rerenderChainPanels = (...args) => panelManager.get('optionChain').refreshIncremental(...args);
window.patchTopBarAndDecision = (...args) => panelManager.get('optionChain').patch(...args);
window.buildOiTopMoversStrip = (...args) => app.oiFlow.buildOiTopMoversStrip(...args);
window.buildOiFlowRows = (...args) => app.oiFlow.buildOiFlowRows(...args);
window.buildOiFlowSummaryHtml = (...args) => app.oiFlow.buildOiFlowSummaryHtml(...args);
window.switchOiFlowTab = (...args) => app.oiFlow.switchOiFlowTab(...args);
window.renderExecutiveDashboard = (...args) => app.exec.renderExecutiveDashboard(...args);
window.buildDriversDraggersCard = (...args) => app.exec.buildDriversDraggersCard(...args);
window.buildFiiDiiCard = (...args) => app.exec.buildFiiDiiCard(...args);
window.buildFiiDiiSummaryCard = (...args) => app.exec.buildFiiDiiSummaryCard(...args);
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
window.openGreeksModal = (...args) => app.modal.openGreeksModal(...args);
window.closeGreeksModal = (...args) => app.modal.closeGreeksModal(...args);
window._greeksEscHandler = (...args) => app.modal._greeksEscHandler(...args);
window.openFiiDiiModal = (...args) => app.modal.openFiiDiiModal(...args);
window.closeFiiDiiModal = (...args) => app.modal.closeFiiDiiModal(...args);
window._fiidiiEscHandler = (...args) => app.modal._fiidiiEscHandler(...args);
window.openIvSurfaceModal = (...args) => app.modal.openIvSurfaceModal(...args);
window.closeIvSurfaceModal = (...args) => app.modal.closeIvSurfaceModal(...args);
window._ivSurfaceEscHandler = (...args) => app.modal._ivSurfaceEscHandler(...args);

// ── Ported from option-chain.js — now just expiry-dropdown population and
// the BroadcastChannel sync to the standalone option-chain.html tab. The
// dense in-dashboard table, its right analytics panel, and their
// currentRange/velocityWindowMin/selectedDepthStrike state have all been
// removed — see ChainView.buildChainSummaryHtml() in chain-views.js for
// the compact snapshot card that replaced them.
window.mapPayloadToRows = (...args) => app.chainDense.mapPayloadToRows(...args);
window.buildVelocityLookup = (...args) => app.chainDense.buildVelocityLookup(...args);
window.renderExpiryOptions = (...args) => app.chainDense.renderExpiryOptions(...args);
window.refreshView = (...args) => panelManager.get('optionChain').refreshDense(...args);

// _fsaHandle / _legacyFile / _autoRefreshTimer / _countdownTimer /
// _renderScheduled / _lastRenderedSymbol: removed (Phase 6 — remaining-
// globals cleanup). All were only ever read/written from inside
// DataService's own methods, which now use this.fsaHandle / this.legacyFile
// / this.autoRefreshTimer / this.countdownTimer / this.renderScheduled /
// this.lastRenderedSymbol directly — no other file ever touched these, so
// the shims had no external consumer left.
Object.defineProperty(window, '_data', {
  configurable: true,
  get() { return app.data.data; },
  set(v) { app.data.data = v; },
});
// _timerMins: removed (Phase 6 — remaining-globals cleanup). DataService
// (data-service.js) now reads/writes this.timerMins on itself directly,
// and UiControls.switchTimer (ui-controls.js) writes app.data.timerMins
// explicitly — no code reads window._timerMins anymore.
Object.defineProperty(window, '_ws', {
  configurable: true,
  get() { return app.data.wsManager.ws; },
  set(v) { app.data.wsManager.ws = v; },
});
// _wsState: removed (Phase 6 — remaining-globals cleanup). Every former
// reader/writer now goes straight through app.data.store.state (the
// MarketStore this shim used to proxy to) or, inside DataService's own
// rAF-scheduled render, a genuine local const — no code reads
// window._wsState anymore, so the shim itself was pure global pollution
// with no consumer left. Same MarketStore instance (app.data.store),
// same data, just no window property in between.
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

// ── GLOBAL EVENT REGISTRATION ──

window.addEventListener('load', updateStickyOffsets);
window.addEventListener('resize', updateStickyOffsets);
// Panel-level resize hooks (Phase 4) — most of the six panels no-op this
// (including PriceChartPanel now that its chart engine lives on
// price-chart.html instead of this page). Kept as a separate listener
// rather than folded into updateStickyOffsets above so a panel's resize
// concern stays owned by its panel, not by UiControls.
window.addEventListener('resize', () => panelManager.resizeAll());

// ── INIT ──
window.addEventListener('load', function(){
  const msg = document.getElementById('err-msg');
  if(msg) msg.textContent = '📂 Click "Open file" to load nifty_dashboard.json — auto-refresh every 5 min starts automatically.';
});

// Click on the dark backdrop (outside the panel) also closes it.
document.addEventListener('DOMContentLoaded', function(){
  var modal = document.getElementById('oi-flow-modal');
  if(modal){
    modal.addEventListener('click', function(e){
      if(e.target === modal) closeOIDashboardModal();
    });
  }
  var greeksModal = document.getElementById('greeks-dashboard-modal');
  if(greeksModal){
    greeksModal.addEventListener('click', function(e){
      if(e.target === greeksModal) closeGreeksModal();
    });
  }
});

// ── AUTO-CONNECT ON LOAD ──
window.addEventListener('DOMContentLoaded', function(){
  const lbl = $i('ws-url-label');
  if(lbl) lbl.textContent = _wsUrl;
  // priceChart.ensureMounted()/hydrateRange() no longer happen on this
  // page — they moved to price-chart-standalone.js's boot(), which runs
  // on the standalone price-chart.html tab instead. PriceChartPanel.init()
  // here now just opens the BroadcastChannel that feeds that tab.
  panelManager.initAll();
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