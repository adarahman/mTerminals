// ============================================================
// app-state.js
// Phase 6 — single shared state singleton, replacing the ad-hoc
// window._wsState / _lastGreeks / _ocChan / selectedDepthStrike
// globals that used to be scattered across dashboard.js's
// Object.defineProperty shim block + a couple of true accidental
// globals (selectedDepthStrike).
//
// Every internal module reads/writes through this object EXPLICITLY
// (AppState.wsState = ...) instead of relying on a bare identifier
// resolving through a window-level shim installed at some later,
// unreliable point in script load order — that ordering dependency
// is what caused the ReferenceErrors when new App() threw before
// reaching dashboard.js's shim block.
//
// Must load before ANY file that references AppState: chain-view.js,
// chain-sync.js, chain-renderer.js, chain-depth.js, ws-manager.js,
// market-store.js, data-service.js, panels-views.js, paper-trading.js,
// dashboard.js. See DashboardPro.html script order — this tag goes
// first, before chart-legend.js.
//
// NOT for the truly public/external surface: inline onclick="" markup
// (e.g. openFiiDiiModal()) still needs its window.* shim in
// dashboard.js's legacy shim block, same as before. AppState only
// replaces internal, same-page module-to-module state.
//
// option-chain.js (the standalone tab, loaded by a different HTML
// document) is intentionally NOT migrated to AppState — it runs in a
// separate tab with its own JS context and can never share this
// object. Its window._ocChan stays as-is; only THIS page's chain-
// sync.js side (which opens/owns the channel) moves to AppState.ocChan.
// ============================================================

const AppState = {
  wsState: null,             // was window._wsState (dashboard.js accessor shim)
  lastGreeks: [],            // was window._lastGreeks (chain-renderer.js)
  ocChan: null,               // was window._ocChan, THIS page's side only (chain-sync.js)
  selectedDepthStrike: null, // was an accidental implicit global (chain-depth.js)
};

window.AppState = AppState; // one deliberate, explicit global instead of four ad-hoc ones
