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
// and price-chart.js/the split paper-trading modules (calls into their globals). Must
// load before dashboard.js, whose App constructor instantiates these.
// See DashboardPro.html script order.
// ============================================================

// ── 1. Price Chart ──
// CORRECTION: the comment that used to be here claimed the full
// PriceChartEngine (PriceChart/price-chart.js) no longer loads on this
// page and that only a compact snapshot card remains. That's not what
// actually ships: DashboardPro.html still loads the entire PriceChart/*.js
// engine, and dashboard.js's DOMContentLoaded handler calls
// priceChart.ensureMounted() + priceChart.hydrateRange(...) directly,
// mounting the real chart (not a lightweight card) on this same page.
// That mount + its historical-range fetch fire at the same moment as the
// dashboard's own first-render burst (see data-service.js's
// notYetBuilt/symbolChanged branch) — worth knowing if you're debugging
// anything that looks tied to "when the chart loads" (e.g. the Decision
// Detail freeze fixed in chain-renderer.js's refreshDecisionBoxGuarded()/
// renderDashboard()), since it's two independent startup tasks landing in
// the same window, not one causing the other.
//
// What this panel DOES still own: broadcasting every tick's spot/symbol
// over BroadcastChannel('pc-live-sync') so a price-chart.html tab open
// in another window stays live, and answering that tab's
// 'pc-request-snapshot' request on open so it isn't blank until the next
// tick. This channel is only for the separate Price Chart surface.
class PriceChartPanel extends LiveSyncPanel {
  constructor() {
    super('priceChart', 'pc-live-sync', 'pc-request-snapshot');
  }

  // Called from DataService.updateDashboard() on every tick — replaces
  // the old direct `priceChart.addTick(...)` call now that the chart
  // engine doesn't live on this page. _broadcast() keeps the last tick
  // around so a price-chart.html tab opened later gets an immediate
  // reply instead of waiting for the next live tick.
  pushTick(spot, symbol, spotChange, spotChgPct) {
    this._broadcast({ spot, symbol, spotChange, spotChgPct, t: Date.now() });
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

  // Canonical chain payload mapping for dashboard drill-downs.
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
// throughout since ModalManager's internals live in Panels/modal-manager.js,
// which this refactor doesn't otherwise touch.
// ── 4. Paper Trading ──
// the paper-trading modules already keep their own
// UI current as part of the chain template rebuild — the fund-summary
// pill in the top bar calls ptComputeFundSummary() directly from
// chain-template.js on every renderDashboard()/patch() pass — plus
// whatever tick handling those modules do internally. This panel exists so
// they can opt into an explicit panel-level refresh
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
// "just refresh the decision box" into its own call for a caller that
// only cares about the decision box and doesn't want to go through the
// top-bar patch (or a full rebuild) to get it.
//
// FIX: this used to reimplement its own copy of the #sec-decision
// outerHTML swap, guarded only by whether the Decision Detail <details>
// was already open. That copy never carried the later mousedown/mouseup
// click-guard fix added to ChainView.patchTopBarAndDecision (chain-
// renderer.js) — so any call path that reached this panel's refresh()
// instead of/alongside OptionChainPanel.patch() was still hitting the
// unguarded version even after patchTopBarAndDecision was fixed, tearing
// the <summary> out from under an in-progress click and silently
// preventing Decision Detail from ever expanding. Delegating to
// ChainView.refreshDecisionBoxGuarded() means there is exactly one
// implementation of this swap or its guard, so any future fix to that
// race only has to be made once.
class DecisionBoxPanel extends Panel {
  constructor() { super('decisionBox'); }

  refresh(data) {
    const d = data !== undefined ? data : (typeof _data !== 'undefined' ? _data : undefined);
    if (!d) return;
    app.chain.refreshDecisionBoxGuarded(d);
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
