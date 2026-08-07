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
  // option-chain.js listens on a BroadcastChannel('oc-live-sync') and
  // posts {type:'oc-request-snapshot'} on load,
  // {type:'oc-request-expiry', expiry} when its dropdown changes, and
  // {type:'oc-request-range', range} when its own ±3/±5/±10/All buttons
  // change — but nothing on this side ever opened that channel (the
  // constructor call was commented out), so every message went nowhere:
  // the tab stayed on 5 rows of demo data forever. With only 5 demo
  // strikes, ±3/±5/±10/All all render the exact same visible rows, which
  // is why the tab's own range button looked broken — it was actually
  // never receiving real data to filter in the first place.
  //
  // _initBroadcast() opens the channel once (from the ChainDenseView
  // constructor) and wires the message listener once — the old code
  // re-added a listener inside _broadcastToOptionChainTab itself, which
  // would have leaked a duplicate listener on every single tick had the
  // channel ever actually been open.
ChainDenseView.prototype._initBroadcast = function() {
    if (!("BroadcastChannel" in window)) return;
    this._ocChan = new BroadcastChannel("oc-live-sync");
    this._ocChan.addEventListener("message", (e) => {
      const msg = e.data;
      if (!msg) return;
      if (msg.type === "oc-request-snapshot") {
        if (this.lastPayload) this._broadcastToOptionChainTab(this.lastPayload);
      } else if (msg.type === "oc-request-expiry") {
        // Drive the switch through the real expiry-change path (same one
        // the global #expirySelect uses) — it updates _data and then
        // itself calls refreshView(), which re-broadcasts below.
        if (window.onExpiryChange) window.onExpiryChange(msg.expiry);
      } else if (msg.type === "oc-request-range") {
        // Same idea for range: drive it through the real sidebar range
        // path so _chainRange (the one global every in-page table/modal
        // already reads via getFilteredChain()/filterRowsByRange()) stays
        // the single source of truth, instead of the tab keeping its own
        // disconnected copy.
        if (window.switchChainRange) window.switchChainRange(msg.range);
      } else if (msg.type === "oc-place-order") {
        // Buy/Sell from the standalone tab's quick-order modal — see
        // option-chain.js's placeOrder(). That tab has no parent window to
        // call ptDispatchOrder() through directly (unlike the embedded-
        // iframe path in panels-views.js), so it routes the request over
        // this same channel instead; answer it here.
        this._handleOcPlaceOrder(msg);
      }
    });
};

ChainDenseView.prototype._broadcastToOptionChainTab = function(payload) {
    if (!this._ocChan) return;

    // ── ATTACH GREEKS ──
    // mapPayloadToRows() (chain-depth.js) never merges Greeks onto a row's
    // ce/pe leg — they stay in the separate payload.greeks array, keyed by
    // strike, which is only ever looked up on the main-dashboard side (see
    // buildRowsHtml's greeksByStrike in chain-renderer.js, used to feed
    // buildChainRowViewModel/buildStrikeDetailViewModel). The standalone
    // Option Chain tab's Greek row (buildGreekRowHtml in option-chain.js)
    // reads leg.delta/gamma/theta/vega directly off r.ce/r.pe though, so
    // without this merge those fields are always undefined on that tab no
    // matter how fresh payload.greeks is. Built fresh here (not mutating
    // this.lastRows in place) since that array is also read by the main
    // dashboard's own render path, which has no use for these fields.
    const greeksByStrike = {};
    (payload.greeks || []).forEach((g) => { greeksByStrike[g.strike] = g; });
    const rowsWithGreeks = (this.lastRows || []).map((r) => {
      const g = greeksByStrike[r.strike] || {};
      return Object.assign({}, r, {
        ce: Object.assign({}, r.ce, { delta: g.cDelta, gamma: g.cGamma, theta: g.cTheta, vega: g.cVega }),
        pe: Object.assign({}, r.pe, { delta: g.pDelta, gamma: g.pGamma, theta: g.pTheta, vega: g.pVega }),
      });
    });

    this._ocChan.postMessage({
      rows: rowsWithGreeks, symbol: payload.symbol, spot: payload.spot,
      // payload's actual field is "spotChange" (see mTerminals_json.py's
      // export) — this used to read payload.spotChg, which never exists,
      // so the standalone tab's state.spotChg was always undefined on
      // every broadcast and applyLivePayload's `if (msg.spotChg != null)`
      // guard silently kept it frozen at its initial demo value forever.
      // spotChgPct's name already matched end-to-end, so only the
      // absolute change was ever stuck, not the percent.
      spotChg: payload.spotChange, spotChgPct: payload.spotChgPct,
      expiry: payload.expiry, expiryDates: payload.expiryDates,
      // The global ATM range, so the tab's own toggle group reflects
      // whatever every other table on the main dashboard is showing —
      // this is what makes the range control "global" rather than each
      // surface keeping its own independent filter.
      range: (typeof _chainRange !== "undefined" ? _chainRange : 3),
      // BUGFIX: maxPain and volOiRatios were never forwarded, so
      // option-chain.js's state.maxPain/state.volOiRatios stayed stuck at
      // their initial null/{} forever (its `if (msg.maxPain != null)` /
      // `if (msg.volOiRatios)` guards never fired). That silently desynced
      // this tab's Smart Money / Market Structure columns from the Strike
      // Detail Report's — the ATM strike never got tagged "Max Pain" here
      // (falling through to a resistance/support rank instead), and Smart
      // Money fell back to a local vol/oi approximation instead of the
      // real server-fed ratio the Strike Detail Report reads. Both fields
      // already exist on payload (see mTerminals_json.py) — just weren't
      // included in this postMessage.
      maxPain: payload.maxPain,
      volOiRatios: payload.volOiRatios,
    });
};

// Answers an {type:"oc-place-order"} request from the standalone tab (see
// placeOrder()'s Path 2 in option-chain.js). msg.order is
// { symbol, expiry, strike, side, ltp, qty, action } where side is
// "CE"/"PE" and action is "BUY"/"SELL" — translate that into the shape
// ptDispatchOrder() (paper-trading.js) already expects everywhere else
// (main form, quick-order popover, strategy legs), then route through it
// so this order shows up in the same Order/Trade Log as everything else.
//
// ptDispatchOrder() is fire-and-forget over the paper-trading WebSocket —
// it does not return a real fill/reject, only whether the request was sent.
// Therefore this bridge MUST NOT translate a successful send into FILLED.
// FILLED is reserved for the actual backend-confirmed order lifecycle.
//
// In live-trading mode ptDispatchOrder() opens the Dashboard's explicit
// confirmation modal and does not send until that confirmation happens.
// The standalone Option Chain is told CONFIRMATION_REQUIRED, not SENT.
ChainDenseView.prototype._handleOcPlaceOrder = function(msg) {
    if (!this._ocChan) return;
    const o = msg.order || {};
    const payload = {
      symbol: o.symbol,
      expiry: o.expiry,
      strike: o.strike,
      instrument_type: o.side,   // "CE" / "PE"
      side: o.action,            // "BUY" / "SELL"
      qty_lots: o.qty,
      order_type: "MARKET",
    };
    const liveNeedsConfirm = typeof ptIsLiveMode === "function" && ptIsLiveMode();
    const ok = ptDispatchOrder(payload, null);
    this._ocChan.postMessage({
      type: "oc-order-result",
      reqId: msg.reqId,
      status: ok ? (liveNeedsConfirm ? "CONFIRMATION_REQUIRED" : "SENT") : "REJECTED",
      reason: ok ? undefined : "WS not connected — order not sent",
    });
};

// ── SEMANTIC NAVIGATION TO D-05 (dedicated Option Chain) ─────────────
// Reuses one named tab/window instead of opening a duplicate chain on every
// click. A #strike hash handles the first-load case; oc-focus-strike over the
// existing BroadcastChannel handles an already-open tab without reloading it.
function openOptionChainAtStrike(strike) {
    const n = Number(strike);
    const hasStrike = Number.isFinite(n);
    const url = '../OptionChain/option-chain.html' + (hasStrike ? `#strike=${encodeURIComponent(n)}` : '');
    const win = window.open(url, 'mterminals-option-chain');
    if (win && typeof win.focus === 'function') { try { win.focus(); } catch(e){} }

    if (hasStrike && typeof app !== 'undefined' && app.chainDense && app.chainDense._ocChan) {
      const sendFocus = () => app.chainDense._ocChan.postMessage({ type:'oc-focus-strike', strike:n });
      // Send immediately for an existing tab and once more after the new tab
      // has had a chance to attach its BroadcastChannel listener.
      sendFocus();
      setTimeout(sendFocus, 300);
    }
    return false;
}

function openOptionChain() {
    return openOptionChainAtStrike(null);
}
