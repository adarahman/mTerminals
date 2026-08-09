// Dense option-chain table status, expiry controls and live row rendering.

// ============================================================
// chain-renderer.js
// Phase 2 chain-view decomposition — see chain-view.js's header comment
// for the full split rationale and load-order requirement (this file
// must load after chain-view.js, and before dashboard.js).
//
// This file holds the methods that actually touch the live DOM: the
// per-tick dense-chain refresh (ChainDenseView), the full-rebuild and
// incremental-patch render paths (ChainView.renderDashboard,
// patchTopBarAndDecision, _rerenderChainPanels, onExpiryChange), and the
// smaller DOM-writing panels (velocity table, IV surface modal, chain
// scroll sizing). Moved verbatim from chain-views.js.
// ============================================================

ChainDenseView.prototype.setStatus = function(live, text) {
    const dot = document.getElementById("statusDot");
    if (dot) dot.classList.toggle("live", live);
    const t = document.getElementById("statusText");
    if (t) t.textContent = text;
};

ChainDenseView.prototype.updateHeader = function(payload) {
    const symbol = payload.symbol || "NIFTY";
    const spot = payload.spot;
    const pcr = payload.totalPCR;
    const maxPain = payload.maxPain;
    let totalCe = 0, totalPe = 0;
    (payload.chain || []).forEach((r) => { totalCe += r.ceOI || 0; totalPe += r.peOI || 0; });

    const expiryLabel = document.getElementById("expiryLabel");
    if (expiryLabel) expiryLabel.textContent = "OPTION CHAIN";
    const h1 = document.querySelector(".head h1");
    if (h1 && h1.firstChild) h1.firstChild.textContent = symbol + " ";
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set("metaSpot", spot != null ? Number(spot).toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "—");
    set("metaDte", payload.dte != null ? payload.dte + "d" : "—");
    set("metaPcr", pcr != null ? Number(pcr).toFixed(2) : "—");
    set("metaMaxPain", maxPain != null ? Number(maxPain).toLocaleString("en-IN") : "—");
    set("metaOiCe", fmt(totalCe));
    set("metaOiPe", fmt(totalPe));
    const ceVelHdr = document.getElementById("hdr-ce-vel");
    const peVelHdr = document.getElementById("hdr-pe-vel");
    if (ceVelHdr) ceVelHdr.textContent = `CE OI VEL (${this.velocityWindowMin}m)`;
    if (peVelHdr) peVelHdr.textContent = `PE OI VEL (${this.velocityWindowMin}m)`;
};

ChainDenseView.prototype.renderExpiryOptions = function(payload) {
    const sel = getExpirySelectNode();
    if (!sel) return;
    const rawDates = payload.expiryDates || [payload.expiry];
    const dates = (typeof sortExpiryDates === "function") ? sortExpiryDates(rawDates) : rawDates;
    const chainStore = payload.chains || {};
    const activeExpiry = payload.expiry || "";

    // A click just fired onExpiryChange and pinned the select to the expiry
    // the user picked (see ChainView.prototype.onExpiryChange), but the
    // connection swap hasn't resolved yet. Until this payload's own expiry
    // actually matches that pick — proof the new connection has landed —
    // don't let a stale/racing payload.expiry (old connection's tail ticks,
    // or a delta that never carries "expiry" at all and falls back to "")
    // stomp the dropdown back. Once it matches, the switch is confirmed and
    // the pending marker is cleared so normal syncing resumes.
    const pending = sel.dataset.pendingExpiry;
    if (pending) {
      // Compare by parsed calendar date, not raw string — if the backend's
      // confirmed payload.expiry never byte-matches what onExpiryChange set
      // as pending (case/format drift), this used to never clear, so the
      // dropdown stayed force-pinned to a stale value on every future
      // render regardless of what the user actually selected.
      if (activeExpiry && typeof parseExpiryDate === "function" && parseExpiryDate(activeExpiry) === parseExpiryDate(pending)) {
        delete sel.dataset.pendingExpiry;
      } else if (activeExpiry && activeExpiry === pending) {
        delete sel.dataset.pendingExpiry;
      } else {
        // Clear pending if it's been too long (5 seconds) to avoid stuck state
        const pendingTime = parseInt(sel.dataset.pendingExpiryTime || '0');
        if (Date.now() - pendingTime > 5000) {
          delete sel.dataset.pendingExpiry;
          delete sel.dataset.pendingExpiryTime;
        } else {
          if (sel.value !== pending) sel.value = pending;
          return;
        }
      }
    }

    const key = dates.join("|");
    if (sel.dataset.optionsKey !== key) {
      sel.innerHTML = dates.map((dt) => {
        const hasData = chainStore[dt] ? true : dt === payload.expiry;
        const bullet = hasData ? "● " : "○ ";
        return `<option value="${dt}"${dt === activeExpiry ? " selected" : ""}>${bullet}${dt}</option>`;
      }).join("");
      sel.dataset.optionsKey = key;
    } else if (activeExpiry && sel.value !== activeExpiry && !sel.dataset.pendingExpiry) {
      // Only sync to activeExpiry if there's no pending switch in progress
      sel.value = activeExpiry;
    }
};

// ── HTML generation split out of business logic (Phase 3) ──
// This function now does ONLY three things: gather the per-render inputs
// that a single row can't compute for itself (maxOI across all rows, the
// strike->greeks lookup), turn each row into a view model
// (buildChainRowViewModel, chain-view-models.js), and render each view
// model to HTML (renderChainRowTemplate, chain-templates.js). No cell
// value, class, or percentage is computed in this function anymore — see
// chain-view-models.js for every calculation that used to live here, and
// chain-templates.js for the markup itself (including the per-strike
// summary row — LTP/chg, IV/chg, OI/chg, OI velocity, volume/%OI,
// bid/ask depth, Greeks, per-leg signal, PCR/PCRchg, combined signal, and
// net GEX — previously built inline here via this.buildStrikeDetailHtml).
ChainDenseView.prototype.buildRowsHtml = function(rows) {
    const tbody = document.getElementById("tbody");
    if (!tbody) return; // dense chain markup not on this page — no-op
    const maxOI = Math.max(1, ...rows.map((r) => Math.max(r.ce.oi || 0, r.pe.oi || 0)));
    // Per-strike Greeks lookup, kept in sync by refreshView()/selectDepthStrike()
    // via AppState.lastGreeks — same payload shape the mini chain panel uses.
    const greeksByStrike = {};
    (AppState.lastGreeks || []).forEach((g) => { greeksByStrike[g.strike] = g; });
    let html = "";
    rows.forEach((r) => {
      const g = greeksByStrike[r.strike] || {};
      const rowVm = buildChainRowViewModel(r, g, maxOI, AppState.selectedDepthStrike);
      html += renderChainRowTemplate(rowVm);
    });
    tbody.innerHTML = html;
};

ChainDenseView.prototype.refreshView = function(payload) {
    // Keep canonical mapped rows available to dashboard drill-downs even
    // though the old duplicate standalone chain has been removed.
    window._lastPayload = payload;
    this.lastPayload = payload;
    renderExpiryOptions(payload);
    window._lastRows = mapPayloadToRows(payload);
    this.lastRows = window._lastRows;
    AppState.lastGreeks = payload.greeks || [];
    this.lastGreeks = AppState.lastGreeks;
    if (!document.getElementById("tbody")) return; // dense chain markup not on this page
    // payload is expected to already reflect the connection's expiry — the
    // server only ever resolves one expiry's chain per connection (see
    // NO_EXTRA_CHAINS in ws_server_live.py), and onExpiryChange reconnects
    // with ?expiry=... rather than swapping expiries out of this payload
    // locally, so there's no separate override step here.
    updateHeader(payload);
    const _visRows = filterRowsByRange(window._lastRows);
    // ── FIXED-HEIGHT CHAIN BOX ──
    // Capture scroll position before the table body is rebuilt below, so a
    // routine WS tick doesn't yank the user back to ATM mid-browse. Only
    // re-center on ATM the first time this table is populated, or when the
    // expiry actually changes — the same "only ~5 (now 7) strikes visible,
    // scroll for the rest" behavior #chain-scroll's CSS already documents.
    const _wrap = $i('chain-scroll');
    const _prevScrollTop = _wrap ? _wrap.scrollTop : null;
    const _expiryChanged = this._lastExpiryKey !== undefined && this._lastExpiryKey !== payload.expiry;
    const _firstRender = this._lastExpiryKey === undefined;
    this._lastExpiryKey = payload.expiry;
    buildRowsHtml(_visRows);
    renderRightPanel(_visRows);
    if (_greeksVisible) document.querySelectorAll('[id^="grk-row-"]').forEach((el) => { el.style.display = ""; });
    if (window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(payload);
    if (_firstRender || _expiryChanged) _centerChainOnATM = true;
    requestAnimationFrame(() => app.chain.sizeAndScrollChain(_prevScrollTop));
};

  // ── FIX: patchTopBarAndDecision was called from scheduleRender() on every
  // WS tick but was never actually defined anywhere in this file (only
  // referenced in the comment above it). Since `window.patchTopBarAndDecision`
  // was always undefined, that `if` silently no-op'd on every tick, so the
  // top-bar spot/badge and the whole Decision Engine box only ever got drawn
  // once — inside the full renderDashboard() rebuild — and looked frozen
  // until a manual page refresh forced that rebuild again. This patches both
  // in place using the exact same templates renderDashboard() uses, so they
  // now stay live tick-to-tick without touching/flickering the rest of the DOM.
  //
  // ── DROPDOWN FIX ──
  // The original fix above still did `topBarEl.outerHTML = this.renderTopBarHtml(d)`
  // on every single tick (several times a second). That destroys and rebuilds
  // the whole top-bar subtree each time, including the symbol <select>
  // (regenerated from an HTML string on every render) and the #expiry-slot
  // the persistent #expirySelect node lives in. Even re-parenting a node
  // into a brand-new slot (moveExpirySelectIntoTopBar) forces the browser to
  // close any currently-open native <select> popup, because the element is
  // being moved in the DOM tree. Net effect: neither dropdown could ever
  // stay open longer than the gap between two ticks — a fraction of a
  // second — no matter how fast you clicked.
  //
  // Fix: only do the destructive full rebuild when the symbol actually
  // changes (new option list, new price scale) or on the very first render.
  // On every other tick, patch just the pieces that legitimately change —
  // spot price, %-badge, index ticker, DTE, time — in place. Both <select>
  // elements are left completely untouched on a normal tick, so an open
  // dropdown stays open and clickable across live updates.
