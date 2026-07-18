// ============================================================
// chain-sync.js
// Phase 2 chain-view decomposition — see chain-view.js's header comment
// for the full split rationale and load-order requirement (this file
// must load after chain-view.js, and before dashboard.js).
//
// This file holds ChainDenseView's BroadcastChannel sync to the
// standalone option-chain.html tab. Moved verbatim from chain-views.js.
// ============================================================

  // ── BROADCAST SYNC to the standalone option-chain.html tab ──
  // option-chain.js already listens on a BroadcastChannel('oc-live-sync')
  // and posts {type:'oc-request-snapshot'} on load and
  // {type:'oc-request-expiry', expiry} when its dropdown changes — but
  // nothing on this side ever opened that channel, so both messages went
  // nowhere: the tab stayed on demo data and its expiry dropdown looked
  // inert. This opens the same channel and answers both message types.
ChainDenseView.prototype._broadcastToOptionChainTab = function(payload) {
    if (!AppState.ocChan) return;   // was: if (!window._ocChan) return;
    AppState.ocChan.postMessage({   // was: window._ocChan.postMessage({
      rows: this.lastRows, symbol: payload.symbol, spot: payload.spot,
    });
    AppState.ocChan.addEventListener("message", (e) => { 
      const msg = e.data;
      if (!msg) return;
      if (msg.type === "oc-request-snapshot") {
        if (this.lastPayload) this._broadcastToOptionChainTab(this.lastPayload);
      } else if (msg.type === "oc-request-expiry") {
        // Drive the switch through the real expiry-change path (same one
        // the global #expirySelect uses) — it updates _data and then
        // itself calls refreshView(), which re-broadcasts below.
        if (window.onExpiryChange) window.onExpiryChange(msg.expiry);
      }
    });
};

ChainDenseView.prototype._broadcastToOptionChainTab = function(payload) {
    if (!this._ocChan) return;
    this._ocChan.postMessage({
      rows: this.lastRows, symbol: payload.symbol, spot: payload.spot,
      spotChg: payload.spotChg, spotChgPct: payload.spotChgPct,
      expiry: payload.expiry, expiryDates: payload.expiryDates,
    });
};
