// ============================================================
// chain-view.js
// Phase 2 chain-view decomposition (see master optimization prompt, Task
// "Chain View decomposition"): chain-views.js had grown too large, so its
// three classes (ChainDenseView, RightPanelView, ChainView) are now split
// across seven files by concern. This file is the MAIN CONTROLLER — it
// owns the three class declarations (constructors + the small stateful
// tab/toggle controller methods that don't build HTML or touch much DOM)
// so every other split-out file can attach its share of methods onto
// ClassName.prototype. This is a pure code-motion refactor: every method
// signature below was moved verbatim from chain-views.js (a class-body
// method `foo(x){...}` is byte-identical in behavior to
// `ClassName.prototype.foo = function(x){...};` — both are plain
// functions on the same prototype, resolved the same way at call time),
// so there is no business-logic, UI, or websocket change here.
//
// LOAD ORDER: this file MUST load first among the chain-*.js split files,
// since the other six (chain-template.js, chain-renderer.js,
// chain-depth.js, chain-greeks.js, chain-sync.js, chain-utils.js) all do
// `ClassName.prototype.method = function(){...}` against the classes
// declared here — that only works if ChainDenseView/RightPanelView/
// ChainView already exist. The other six can load in any order relative
// to each other, but all six must load before dashboard.js, since
// dashboard.js's App constructor does `new ChainDenseView()` /
// `new ChainView()` and expects every prototype method already attached.
// See DashboardPro.html script order.
// ============================================================

// Tracks whether the per-strike Greeks rows are expanded. Toggled by
// ChainView.toggleGreeks(); read on every render (renderDashboard,
// ChainDenseView.refreshView, switchChainRange) to keep expanded rows
// visible across re-renders instead of resetting to collapsed.
let _greeksVisible = false;

// Tracks which strike's Bid/Ask depth box is pinned in the (currently
// unmounted — no page ships #tbody/#rightPanel today, see
// ChainDenseView.refreshView's early-return) right analytics panel.
// Was an undeclared bare `selectedDepthStrike` assignment (implicit
// window global); now explicit module state shared by
// ChainDenseView.selectDepthStrike (chain-depth.js), RightPanelView's
// buildDepthBoxHtml (chain-depth.js), and buildRowsHtml's row-vm mapping
// (chain-renderer.js) the same way _greeksVisible already is.
let _selectedDepthStrike = null;

class ChainDenseView {
  constructor() {
    this.currentRange = "10";
    this.velocityWindowMin = 5;
    this.prevSnapshot = {};
    // Previously ad-hoc window._lastPayload/_lastRows/_lastGreeks globals —
    // now this.lastPayload/lastRows/lastGreeks on the instance, so this
    // view's state lives in one place, same as every other panel's app.*
    // state. Cross-class readers (e.g. ChainView.switchChainRange/
    // switchVelTab below) read app.chainDense.lastX instead of window._lastX.
    this.lastPayload = null;
    this.lastRows = null;
    this.lastGreeks = [];
    this._initBroadcast();
  }
}

class RightPanelView {
  constructor() {
    this.selectedDepthStrike = null;
  }
}

class ChainView {
  constructor() {
    this.velWin = 5;
    this.centerChainOnATM = true;
    this.grkView = 'delta';
    this.chainRange = 10;
    this.greeksVisible = false;
    this.pcrVisible = false;
    this.selStrike = null;
    this.selectedExpiry = null;
    this.expiryViewCache = {};
    // Last spot value actually rendered — compared against the incoming
    // tick each render to pick a flash-up/flash-down class (see
    // renderTopBarHtml). Lives on the instance, not a module-level var,
    // since this class is used as a singleton (app.chain) that persists
    // across ticks even though the DOM node it renders into doesn't.
    this._lastSpot = null;
  }

  updateStickyNav(d){
  const nav=$i('sec-nav-bar');if(!nav)return;
  const strats=d&&d.strategies&&d.strategies.length;
  const risk=d&&d.risk&&(d.risk.keyLevels||d.risk.ivRegime||d.risk.tradeGrade);
  const hasDec=d&&d.decision&&(d.decision.bias||d.decision.confidence);
  document.querySelectorAll('.sec-btn-strats').forEach(b=>b.style.display=strats?'':'none');
  document.querySelectorAll('.sec-btn-risk').forEach(b=>b.style.display=risk?'':'none');
  document.querySelectorAll('.sec-btn-decision').forEach(b=>b.style.display=hasDec?'':'none');
}

  toggleGreeks(){
  _greeksVisible=!_greeksVisible;
  document.querySelectorAll('[id^="grk-row-"]').forEach(el=>{el.style.display=_greeksVisible?'':'none';});
  const icon=$i('grk-toggle-icon');
  const btn=$i('grk-toggle-btn');
  if(icon)icon.textContent=_greeksVisible?'▼':'▶';
  if(btn)btn.classList.toggle('on',_greeksVisible);
}

  toggleGreekRow(strike){
  const el=$i('grk-row-'+strike);
  if(!el)return;
  el.style.display=el.style.display==='none'?'':'none';
}

  switchChainRange(range, el) {
  _chainRange = range;
  _centerChainOnATM = true;

  ['range-tabs-chain','range-tabs-side','range-tabs-grk','range-tabs-iv'].forEach(gid => {
    const g = document.getElementById(gid);
    if(!g) return;
    g.querySelectorAll('.tab-btn').forEach(b => {
      b.classList.toggle('active-range', b.textContent.trim() === (range === 9999 ? 'All' : '±' + range));
    });
  });

  // ── KEEP THE DENSE CHAIN TABLE (#tbody/#rightPanel) IN SYNC ──
  // It used to have its own ±3/±7/±13/ALL filter bar; that's gone, so it
  // now just follows whatever range the global sidebar selects.
  if (typeof currentRange !== 'undefined') {
    currentRange = (range === 9999) ? 'all' : String(range);
    if (app.chainDense.lastRows) {
      const _visRows = filterRowsByRange(app.chainDense.lastRows);
      buildRowsHtml(_visRows);
      renderRightPanel(_visRows);
      requestAnimationFrame(() => app.chain.sizeAndScrollChain(null));
    }
  }

  if(_data) _rerenderChainPanels();

  // Push the new range to the option-chain tab right away — everything
  // inside this page (Greeks/FII-DII/IV-Surface modals, dense table if
  // present) already reads the shared _chainRange global via
  // getFilteredChain()/filterRowsByRange(), so this button is already
  // "global" to those. The standalone option-chain.html tab is a
  // separate window though, so it only finds out via BroadcastChannel —
  // and would otherwise have to wait for the next live tick to hear
  // about it via refreshView()'s regular broadcast.
  if (app.chainDense && app.chainDense.lastPayload) {
    app.chainDense._broadcastToOptionChainTab(app.chainDense.lastPayload);
  }
}

  switchVelTab(win, el) {
  _velWin = win;
  ['vel-tabs-chain','vel-tabs-side'].forEach(gid => {
    const g = document.getElementById(gid);
    if(!g) return;
    g.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active-vel'));
    g.querySelectorAll('.tab-btn').forEach(t => {
      if(t.textContent.trim() === win + 'm') t.classList.add('active-vel');
    });
  });

  // ── KEEP THE DENSE CHAIN TABLE (#tbody/#rightPanel) IN SYNC ──
  // It used to have its own 5m/15m/30m toggle; that's gone, so it now
  // just follows whatever window the global sidebar selects.
  if (typeof velocityWindowMin !== 'undefined' && app.chainDense.lastPayload) {
    velocityWindowMin = win;
    refreshView(app.chainDense.lastPayload);
  }

  if(_data) _rerenderChainPanels();
}

  switchGrkTab(view,el){
  _grkView=view;
  document.querySelectorAll('#grk-tabs .tab-btn').forEach(t=>t.classList.remove('active-grk'));
  el.classList.add('active-grk');
  renderGreeksGex(view);
}
}