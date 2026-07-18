// ============================================================
// dashboard-panels.js
// Phase 4 (see master optimization prompt, Task "Introduce Panel
// Manager"). Companion to panel-manager.js — see that file's header for
// the Panel/PanelManager contract this implements against.
//
// Each class below wraps ONE of the six panels named in the task
// (PriceChart, OptionChain, OI Dashboard, Paper Trading, Decision Box,
// Market Breadth) behind the same init/refresh/resize/destroy contract.
// This is a wrapping layer, not a rewrite: every method here delegates to
// the exact same class method or global function the pre-PanelManager
// code already called (app.chain.renderDashboard, priceChart.
// ensureMounted, etc.) — see each panel's comment for exactly which call
// it replaces. Existing behavior is unchanged; what changes is that
// dashboard.js now reaches every panel through one registry instead of a
// long flat list of bespoke global shims.
//
// Instantiated and registered in dashboard.js's App constructor, where
// `app` (the App instance) already exists as a module-level const by the
// time any of these methods actually runs — see that file.
//
// Must load after panel-manager.js (extends Panel) and after chain-
// view.js/chain-renderer.js/chain-depth.js/chain-greeks.js/panels-views.js
// (calls into ChainView/ChainDenseView/OiFlowView/ModalManager instances)
// and price-chart.js/paper-trading.js (calls into their globals). Must
// load before dashboard.js, whose App constructor instantiates these.
// See DashboardPro.html script order.
// ============================================================

// ── 1. Price Chart ──
// Wraps the standalone `priceChart` module (price-chart.js). That file
// isn't touched by this refactor — this panel only gives its two known
// entry points (ensureMounted/hydrateRange, previously called ad hoc from
// dashboard.js's DOMContentLoaded listener) a lifecycle home.
class PriceChartPanel extends Panel {
  constructor() { super('priceChart'); }

  init() {
    super.init();
    if (typeof priceChart === 'undefined' || !priceChart) return;
    priceChart.ensureMounted();
    // Backfill from the last few minutes of history before the WS starts
    // pushing live ticks — fire-and-forget, same as the original
    // DOMContentLoaded call (see dashboard.js history: hydrateRange()
    // no-ops safely if a live tick wins the race or the endpoint errors).
    priceChart.hydrateRange(priceChart.settings.range);
  }

  refresh(data) {
    // price-chart.js drives its own updates from live WS ticks
    // internally — nothing in the pre-PanelManager code ever called an
    // explicit "refresh the chart" entry point, so this stays a guarded
    // no-op unless/until price-chart.js exposes one.
    if (typeof priceChart !== 'undefined' && priceChart && typeof priceChart.refresh === 'function') {
      priceChart.refresh(data);
    }
  }

  resize() {
    if (typeof priceChart !== 'undefined' && priceChart && typeof priceChart.resize === 'function') {
      priceChart.resize();
    }
  }

  destroy() {
    super.destroy();
    if (typeof priceChart !== 'undefined' && priceChart && typeof priceChart.destroy === 'function') {
      priceChart.destroy();
    }
  }
}

// ── 2. Option Chain ──
// Wraps ChainView (app.chain) + ChainDenseView (app.chainDense) — the
// dense in-page table's payload mapping/broadcast, the full-page
// rebuild, the per-tick top-bar+decision patch, and the expiry-switch
// incremental refresh. Four different "refresh" granularities already
// existed before PanelManager (renderDashboard / patchTopBarAndDecision /
// _rerenderChainPanels / refreshView) — rather than collapsing them into
// one and guessing which call sites need which, each is exposed as its
// own method here; refresh() maps to the full rebuild since that's the
// correct default when a caller doesn't know which granularity it needs.
class OptionChainPanel extends Panel {
  constructor() { super('optionChain'); }

  // Full rebuild — was `window.renderDashboard(data)`.
  refresh(data) {
    const d = data !== undefined ? data : (typeof _data !== 'undefined' ? _data : undefined);
    if (d !== undefined) app.chain.renderDashboard(d);
  }

  // Lightweight per-tick patch (top-bar spot/badge/ticker + decision box
  // only) — was `window.patchTopBarAndDecision(data)`.
  patch(data) {
    app.chain.patchTopBarAndDecision(data);
  }

  // Expiry-switch incremental refresh of every chain-dependent section —
  // was `window._rerenderChainPanels()`.
  refreshIncremental() {
    app.chain._rerenderChainPanels();
  }

  // Dense chain payload mapping + BroadcastChannel push to the standalone
  // option-chain.html tab — was `window.refreshView(payload)`.
  refreshDense(payload) {
    app.chainDense.refreshView(payload);
  }

  resize() {
    if (typeof app.chain.sizeAndScrollChain === 'function') {
      requestAnimationFrame(() => app.chain.sizeAndScrollChain(null));
    }
  }
}

// ── 3. OI Dashboard ──
// The OI Flow summary card and its Butterfly-tab data are already rebuilt
// as part of OptionChainPanel's refresh()/refreshIncremental() (they call
// buildOiFlowSummaryHtml/buildOiTopMoversStrip internally, same as
// before) — this panel's own responsibility is just the modal
// (ModalManager, app.modal): pushing fresh data into the OI Dashboard
// iframe while it's open, and lifecycle for opening/closing it. Guarded
// throughout since ModalManager's internals live in panels-views.js,
// which this refactor doesn't otherwise touch.
class OiDashboardPanel extends Panel {
  constructor() { super('oiDashboard'); }

  refresh(data) {
    if (app.modal && typeof app.modal.pushOiDashboardData === 'function') {
      app.modal.pushOiDashboardData(data);
    }
  }

  destroy() {
    super.destroy();
    if (app.modal && typeof app.modal.closeOIDashboardModal === 'function') {
      app.modal.closeOIDashboardModal();
    }
  }
}

// ── 4. Paper Trading ──
// paper-trading.js (not touched by this refactor) already keeps its own
// UI current as part of the chain template rebuild — the fund-summary
// pill in the top bar calls ptComputeFundSummary() directly from
// chain-template.js on every renderDashboard()/patch() pass — plus
// whatever tick handling paper-trading.js does internally. This panel
// exists so paper-trading.js can opt into an explicit panel-level refresh
// hook later without this file needing to know its internals; today it's
// a guarded no-op unless that hook exists.
class PaperTradingPanel extends Panel {
  constructor() { super('paperTrading'); }

  refresh(data) {
    if (typeof window.ptRefreshPanel === 'function') window.ptRefreshPanel(data);
  }
}

// ── 5. Decision Box ──
// The Decision Engine box (#sec-decision) was previously only patched as
// a side effect of patchTopBarAndDecision()'s combined top-bar+decision
// tick update, or rebuilt wholesale inside renderDashboard(). Both of
// those still happen unchanged via OptionChainPanel — this panel decouples
// "just refresh the decision box" into its own call using the exact same
// template method (ChainView.renderDecisionBoxHtml) and the exact same
// outerHTML swap patchTopBarAndDecision already does, so a caller that
// only cares about the decision box doesn't need to go through the
// top-bar patch (or a full rebuild) to get it.
class DecisionBoxPanel extends Panel {
  constructor() { super('decisionBox'); }

  refresh(data) {
    const d = data !== undefined ? data : (typeof _data !== 'undefined' ? _data : undefined);
    if (!d) return;
    const decEl = document.getElementById('sec-decision');
    if (decEl) decEl.outerHTML = app.chain.renderDecisionBoxHtml(d);
  }
}

// ── 6. Market Breadth ──
// No Market Breadth data source or rendering function exists yet anywhere
// in the current codebase (checked chain-renderer.js, chain-greeks.js,
// chain-template.js, panels-views.js — nothing computes or displays
// advance/decline counts, sector breadth, or similar). This panel is
// registered as a stub so the slot and lifecycle exist and every other
// panel's registration order/behavior doesn't have to change the day
// breadth data shows up — wire it with setRenderer(fn) once a data
// source and template exist; until then refresh() is a no-op.
class MarketBreadthPanel extends Panel {
  constructor() {
    super('marketBreadth');
    this._renderer = null;
  }

  setRenderer(fn) { this._renderer = fn; }

  refresh(data) {
    if (this._renderer) this._renderer(data);
  }
}
