/* ════════════════════════════════════════════════════════════════════
   PRICE CHART — standalone page bootstrap

   price-chart.js (the PriceChartEngine / `priceChart` global) is loaded
   completely unchanged from the embedded version — this file's only job
   is to open its OWN WebSocket connection straight to the backend and
   mount the chart into #dashboard, same two calls PanelManager.initAll()
   used to make on the main page.

   This page is fully self-contained: it does NOT depend on the main
   DashboardPro.html tab being open. Earlier this used a
   BroadcastChannel('pc-live-sync') relay and just sat on "Waiting for
   dashboard tab…" until that other tab posted a tick — now it connects
   directly to ws://<host>/ws, same URL DataService (data-service.js)
   uses on the main page, and gets the full live feed on its own.

   WSManager (ws-manager.js) + MarketStore (market-store.js) are reused
   as-is — they have no dependency on dashboard.js/DataService, only on
   the global err()/$i() helpers from dom-utils.js, which this page also
   loads. See price-chart.html's script list.

   Historical bars still come from price-chart.js's own
   hydrateRange() -> GET /api/history?... — same-origin fetch, so this
   works whether the page was opened via window.open() or navigated to
   directly.
   ════════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  let wsManager = null;
  let store = null;

  function setConnLabel(live) {
    const dot = document.getElementById("pcsConnDot");
    const lbl = document.getElementById("pcsConnLabel");
    if (dot) dot.classList.toggle("pcs-live", !!live);
    if (lbl) lbl.textContent = live ? "Live" : "Connecting…";
  }

  function updateHeaderReadout(state) {
    const symEl = document.getElementById("pcsSymbol");
    const spotEl = document.getElementById("pcsSpot");
    const chgEl = document.getElementById("pcsChg");
    if (symEl && state.symbol) symEl.textContent = state.symbol;
    if (spotEl && state.spot != null) spotEl.textContent = fmtI(state.spot);
    if (chgEl && state.spotChange != null && state.spotChgPct != null) {
      const pos = state.spotChange >= 0;
      chgEl.textContent = `${pos ? "▲" : "▼"} ${pos ? "+" : ""}${fmtI(state.spotChange)} (${pos ? "+" : ""}${state.spotChgPct.toFixed(2)}%)`;
      chgEl.classList.toggle("pc-pos", pos);
      chgEl.classList.toggle("pc-neg", !pos);
    }
  }

  // Same shape/handling as DataService.updateDashboard()'s price-chart
  // feed in data-service.js — client-side timestamp, no VWAP (index spot
  // has no volume of its own; see that file's comment for why).
  function onStateChange(state) {
    if (!state) return;
    if (!window.AppState.wsState) window.AppState.wsState = {};
    window.AppState.wsState = state;

    if (state.spot != null) {
      priceChart.addTick(state.spot, Date.now(), null);
    }
    // Always render, even with no fresh spot. render() itself resolves the
    // real symbol and (re-)triggers history hydration for it — outside
    // trading hours the feed may never send a new spot at all, so gating
    // this call on state.spot meant render() (and everything inside it)
    // never ran again after boot()'s single data-less call, leaving the
    // chart permanently empty even once real historical bars existed.
    priceChart.render();
    updateHeaderReadout(state);
    setConnLabel(true);
  }

  function connect() {
    store = new MarketStore();
    store.on('change', onStateChange);

    const _symParam = new URLSearchParams(location.search).get('symbol');
    // Mixed-content guard: a page loaded over https: cannot open a plain
    // ws:// socket — browsers block it and `new WebSocket(...)` throws
    // synchronously before any open/close event ever fires, which is what
    // left the connection stuck on "Connecting…" with no visible error.
    // Match the socket's scheme to the page's own scheme instead of
    // hardcoding ws:.
    const _wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    wsManager = new WSManager(`${_wsProto}//${location.host}/ws` + (_symParam ? `?symbol=${encodeURIComponent(_symParam)}` : ''));
    wsManager.on('open', () => setConnLabel(true));
    wsManager.on('close', () => setConnLabel(false));
    wsManager.on('message', (raw) => store.ingest(raw));
    wsManager.connect();
  }

  function wireBackButton() {
    const btn = document.getElementById("pcsBack");
    if (!btn) return;
    btn.onclick = () => {
      if (window.opener) window.close();
      // DashboardPro.html lives one level up now that this page moved
      // into PriceChart/ — was a same-folder "DashboardPro.html" before.
      else window.location.href = "../DashboardPro.html";
    };
  }

  // Resolve the symbol synchronously, before anything touches the network.
  // hydrateRange()/render() both fall back to PRICE_CHART_DEFAULT_SYMBOL
  // (price-chart.js) when AppState.wsState.symbol is unset, so this isn't
  // strictly required for the blank-chart bug — but seeding it here means
  // a ?symbol=BANKNIFTY link works from the very first paint, rather than
  // only after a live WS message arrives (which, on a closed market, may
  // never happen at all).
  function seedInitialSymbol() {
    window.AppState = window.AppState || {};
    if (window.AppState.wsState && window.AppState.wsState.symbol) return;
    const fromUrl = new URLSearchParams(location.search).get('symbol');
    window.AppState.wsState = window.AppState.wsState || {};
    window.AppState.wsState.symbol = fromUrl || PRICE_CHART_DEFAULT_SYMBOL;
  }

  function boot() {
    seedInitialSymbol();
    priceChart.ensureMounted();
    priceChart.hydrateRange(priceChart.settings.range);
    wireBackButton();
    setConnLabel(false);
    connect();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();