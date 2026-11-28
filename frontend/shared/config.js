// ============================================================
// shared/config.js
// Central configuration. Any value that was previously a magic number or
// string literal repeated across files (WS URL, API paths, polling/
// refresh intervals, toast/modal timings) lives here instead.
//
// Load this before any file that references `Config` — put it near the
// top of DashboardPro.html's <script> block, alongside app-state.js/
// event-bus.js.
//
// Usage:
//   this.wsManager = new WSManager(Config.ws.url);
//   fetch(Config.api.history + `?symbol=${sym}&range=${range}`)
//   setInterval(tick, Config.refresh.mockTickMs)
// ============================================================

const _fileHttpOrigin = location.protocol === 'file:' ? 'http://127.0.0.1:5500' : '';
// The backend serves its static dashboard and /ws endpoint on one HTTP port.
// Keep the WebSocket same-origin so custom backend ports work without a
// separate port setting.
const _wsOrigin = location.protocol === 'file:'
  ? 'ws://127.0.0.1:5500'
  : `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}`;

// Restore the last-selected market-data provider into the initial WS URL so
// a page refresh keeps the user's broker choice (matches how
// dashboard.commoditySymbol/dashboard.instrumentMode persist in
// chain/market-context.js). Invalid/stale values are ignored; the backend
// validates the provider anyway.
let _wsUrl = `${_wsOrigin}/ws`;
try {
  const _savedDs = String(localStorage.getItem('dashboard.dataSource') || '').toUpperCase();
  if (/^[A-Z0-9_]+$/.test(_savedDs)) {
    _wsUrl += `?dataSource=${encodeURIComponent(_savedDs)}`;
  }
} catch (_) {}

const Config = {
  ws: {
    // Same-origin WS endpoint shared by the dashboard and Price Chart page.
    url: _wsUrl,
    // Delay before a dropped dashboard connection is retried.
    reconnectDelayMs: 3000,
    // Dashboard feed-health threshold. The engine normally emits at least
    // SmartAPI contracts can legitimately remain unchanged for several
    // seconds. Use 30s so sparse ticks do not make the badge oscillate,
    // while a genuinely frozen stream is still surfaced promptly.
    staleAfterMs: 30000,
    // Minimum spacing between forced replacements of a socket that remains
    // open at the transport layer but stops delivering market snapshots.
    staleRecoveryCooldownMs: 30000,
    // ws_server_live.py's bridge_ws_handler — separate endpoint from /ws,
    // same origin/port. Used by fiidii-report.js's FiiDiiReportFeed
    // (connects only while the FII/DII modal is open).
    relayUrl: `${_wsOrigin}/dashboard-relay`,
  },

  api: {
    history: `${_fileHttpOrigin}/api/history`,
    symbols: `${_fileHttpOrigin}/api/symbols`,
    lotSizes: `${_fileHttpOrigin}/api/lot-sizes`,
    currencyQuote: `${_fileHttpOrigin}/api/currency/quote`,
    currencyOptionChain: `${_fileHttpOrigin}/api/currency/option-chain`,
  },

  refresh: {
    // Default auto-refresh interval for the "Open file" JSON polling
    // path in data-service.js (DataService.timerMins was hardcoded to 5).
    defaultAutoRefreshMins: 5,
    // Mock/demo tick cadence used by simulation helpers.
    mockTickMs: 1000,
    mockJitterMs: 3000,
  },

  ui: {
    // Toast / inline-status message lifetimes (ms).
    toastShortMs: 900,
    toastMediumMs: 1200,
    toastLongMs: 1500,
    toastStatusMs: 2000,
    // Fade-out + DOM removal delay for dismissible elements.
    fadeOutMs: 220,
    // Debounce-style delays used around order submission / render settle.
    orderSubmitSettleMs: 900,
    renderSettleMs: 100,
  },
};

// Expose for both classic <script> tag usage (window.Config) and any
// future module/bundled usage (module.exports), matching how
// formatters.js and app-state.js are already consumed in this codebase.
if (typeof window !== 'undefined') window.Config = Config;
if (typeof module !== 'undefined' && module.exports) module.exports = Config;
