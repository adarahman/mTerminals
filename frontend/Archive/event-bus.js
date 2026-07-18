// ============================================================
// event-bus.js
// Phase 5 (see master optimization prompt, "Event Bus"): reduce coupling
// between modules that currently call each other directly (e.g.
// chain-views.js reaching into app.chainDense.refreshView(), price-chart
// calling into DOM ids owned by other panels).
//
// EventBus is a single, dependency-free pub/sub channel that sits between
// producers (WSManager -> MarketStore, user-driven symbol/expiry
// switches, chart redraws) and any interested subscriber:
//
//   WebSocket -> MarketStore -> EventBus -> Subscribers
//
// This phase only INTRODUCES the bus and PUBLISHES the five topics below
// at their natural origin points. No existing call site, listener, or
// render path was removed or reordered — every emit() added by this
// phase is a pure side-effect bolted onto code that already ran exactly
// as it did before, so today nothing subscribes yet and behavior is
// unchanged. Future phases can migrate direct calls (e.g.
// app.chainDense.refreshView(), priceChart.render() from other modules)
// to eventBus.on(...) instead, one at a time, without touching producers.
//
// Topics published so far:
//   market:update  — MarketStore finished folding a WS message into state
//                     (market-store.js, MarketStore.ingest())
//   symbol:change  — user switched the active index/scrip
//                     (chain-helpers.js, switchActiveIndex())
//   expiry:change  — user switched the selected expiry
//                     (chain-views.js, ChainView.onExpiryChange())
//   chain:update   — the chain-derived panels (table/right panel/Greeks/
//                     GEX/etc.) finished a re-render
//                     (chain-views.js, ChainView._rerenderChainPanels())
//   chart:refresh  — the live price chart finished a redraw
//                     (price-chart.js, PriceChartEngine.render())
//
// Must load BEFORE ws-manager.js/market-store.js/chain-helpers.js/
// chain-views.js/price-chart.js — all of them do a top-level
// `if (window.eventBus) window.eventBar.emit(...)` guard, but the guard
// only protects against EventBus not existing yet; loading it first
// keeps that guard always true. See DashboardPro.html script order.
// ============================================================

class EventBus {
  constructor() {
    this.listeners = {};
  }

  // Subscribe fn to topic. Returns an unsubscribe function so callers
  // don't have to keep the original fn reference around just to remove it.
  on(topic, fn) {
    (this.listeners[topic] || (this.listeners[topic] = [])).push(fn);
    return () => this.off(topic, fn);
  }

  off(topic, fn) {
    if (!this.listeners[topic]) return;
    this.listeners[topic] = this.listeners[topic].filter(f => f !== fn);
  }

  // Publish payload to every current subscriber of topic. No-op (not an
  // error) when nobody has subscribed yet — every emit() this phase adds
  // is fired unconditionally from code that has to run anyway.
  emit(topic, payload) {
    (this.listeners[topic] || []).forEach(fn => {
      try { fn(payload); }
      catch (e) { console.error(`[eventBus] subscriber to "${topic}" threw:`, e); }
    });
  }
}

const eventBus = new EventBus();
window.eventBus = eventBus;
