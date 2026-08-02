// ============================================================
// panel-manager.js
// Phase 4 (see master optimization prompt, Task "Introduce Panel
// Manager"). Defines the Panel base class and the PanelManager registry
// that dashboard.js's App constructor wires up, and that every class in
// dashboard-panels.js extends/implements against.
//
// Contract:
//   Panel        — base class with init/refresh/resize/destroy lifecycle
//                  hooks. Subclasses override whichever hooks apply and
//                  call super.init()/super.destroy() to keep `mounted`
//                  bookkeeping correct (dashboard-panels.js relies on
//                  this for PriceChartPanel/OiDashboardPanel).
//   PanelManager — flat registry keyed by each panel's `name`. Exposes
//                  register/get plus initAll/resizeAll/destroyAll
//                  "fan-out" calls so dashboard.js (and any other caller)
//                  can hit every panel through one object instead of
//                  manually touching app.chain/app.chainDense/app.modal/
//                  ... . register() only adds the panel to the registry —
//                  it does NOT call init() itself, since dashboard.js
//                  registers all six panels inside the App constructor
//                  (before the DOM/other globals like priceChart are
//                  necessarily ready) and only calls panelManager.
//                  initAll() later, explicitly, from its own
//                  DOMContentLoaded listener.
//
// Must load before dashboard-panels.js (whose Panel subclasses extend
// this file's Panel) and before dashboard.js (whose App constructor does
// `new PanelManager()`). See DashboardPro.html script order.
// ============================================================

class Panel {
  constructor(name) {
    this.name = name;
    this.mounted = false;
  }

  // Called once, when the panel manager registers/initializes this
  // panel. Subclasses that override init() should call super.init()
  // first so `mounted` bookkeeping stays correct.
  init() {
    this.mounted = true;
  }

  // Called on every data update. Default is a no-op — most panels
  // override this; ones that don't (yet) just sit idle.
  refresh(data) {
    // no-op by default
  }

  // Called on viewport/container size changes. Default is a no-op.
  resize() {
    // no-op by default
  }

  // Called on teardown (e.g. panel manager reset, symbol switch that
  // tears down and rebuilds panels). Subclasses that override destroy()
  // should call super.destroy() so `mounted` bookkeeping stays correct.
  destroy() {
    this.mounted = false;
  }
}

// Base class for panels that broadcast their latest payload over a
// BroadcastChannel so a standalone tab (price-chart.html) can request a
// snapshot on open instead of sitting blank until the next live tick.
// PriceChartPanel is the one surviving subclass — FiiDiiPanel used to
// extend this for a 'fd-live-sync' channel meant for a standalone
// FII-DII.html tab, but that tab never actually listened on it (it opened
// its own direct WebSocket instead — see price-chart-standalone.js's
// header comment for how price-chart.html later did the same). FII-DII.
// html itself is now gone: its panels live in DashboardPro's own FII/DII
// modal (fiidii-report.js), fed by their own dedicated connection instead
// of this broadcast pattern.
class LiveSyncPanel extends Panel {
  constructor(name, channelName, requestType) {
    super(name);
    this._channelName = channelName;
    this._requestType = requestType;
    this._chan = null;
    this._lastPayload = null;
  }

  init() {
    super.init();
    if (!('BroadcastChannel' in window)) return;
    this._chan = new BroadcastChannel(this._channelName);
    this._chan.addEventListener('message', (e) => {
      const msg = e.data;
      if (msg && msg.type === this._requestType && this._lastPayload) {
        this._chan.postMessage(this._lastPayload);
      }
    });
  }

  // Subclasses call this from their own pushX() method with the exact
  // payload shape their standalone tab expects (e.g. { spot, symbol, ... }
  // for price chart, { fiiDiiSentiment, t } for FII/DII).
  _broadcast(payload) {
    this._lastPayload = payload;
    if (this._chan) this._chan.postMessage(payload);
  }

  destroy() {
    super.destroy();
    if (this._chan) { this._chan.close(); this._chan = null; }
  }
}

class PanelManager {
  constructor() {
    this._panels = new Map();
  }

  // Registers a panel instance, keyed by its `name`. Does NOT call
  // init() — see the class-level note above for why: dashboard.js calls
  // panelManager.initAll() itself, separately, once the DOM is ready.
  register(panel) {
    if (!panel || !panel.name) return;
    this._panels.set(panel.name, panel);
    return panel;
  }

  // Look up a registered panel by name — useful for callers that need a
  // specific panel's non-lifecycle methods (e.g. OptionChainPanel.patch/
  // refreshIncremental/refreshDense), which aren't part of the common
  // initAll/resizeAll/destroyAll fan-out below.
  get(name) {
    return this._panels.get(name);
  }

  // Fan out init() to every registered panel — called once by
  // dashboard.js from its DOMContentLoaded listener (was: the two direct
  // priceChart.ensureMounted()/hydrateRange() calls in that same
  // listener; every other panel's init() is a no-op today).
  initAll() {
    for (const panel of this._panels.values()) {
      try {
        panel.init();
      } catch (err) {
        Logger.error('panel-manager', `init failed for "${panel.name}":`, err);
      }
    }
  }

  // Fan out a data refresh to every registered panel. Any panel whose
  // refresh() throws is caught and logged so one broken panel can't take
  // the rest of the dashboard down with it.
  refreshAll(data) {
    for (const panel of this._panels.values()) {
      try {
        panel.refresh(data);
      } catch (err) {
        Logger.error('panel-manager', `refresh failed for "${panel.name}":`, err);
      }
    }
  }

  // Fan out a resize to every registered panel — called by dashboard.js's
  // window 'resize' listener.
  resizeAll() {
    for (const panel of this._panels.values()) {
      try {
        panel.resize();
      } catch (err) {
        Logger.error('panel-manager', `resize failed for "${panel.name}":`, err);
      }
    }
  }

  // Fan out teardown to every registered panel, then clear the registry.
  destroyAll() {
    for (const panel of this._panels.values()) {
      try {
        panel.destroy();
      } catch (err) {
        Logger.error('panel-manager', `destroy failed for "${panel.name}":`, err);
      }
    }
    this._panels.clear();
  }
}
