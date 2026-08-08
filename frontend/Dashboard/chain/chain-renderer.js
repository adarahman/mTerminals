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
    // Everything below this point (expiry options, row mapping, and the
    // BroadcastChannel push to option-chain.html) must run regardless of
    // whether the dense in-dashboard chain table exists on this page — it
    // no longer does on the main dashboard (moved to option-chain.html),
    // but the main dashboard is exactly the page that has to keep
    // computing rows and broadcasting them for that standalone tab to stay
    // live. Only the actual table-DOM writes further down are page-specific.
    window._lastPayload = payload;
    this.lastPayload = payload;
    renderExpiryOptions(payload);
    window._lastRows = mapPayloadToRows(payload);
    this.lastRows = window._lastRows;
    AppState.lastGreeks = payload.greeks || [];
    this.lastGreeks = AppState.lastGreeks;
    this._broadcastToOptionChainTab(payload);

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
ChainView.prototype.patchTopBarAndDecision = function(d) {
  if (!d) return;
  const topBarEl = document.getElementById('sec-topbar');
  const symbolChanged = !topBarEl || d.symbol !== this._lastTopBarSymbol;
  this._lastTopBarSymbol = d.symbol;

  if (symbolChanged) {
    if (topBarEl) topBarEl.outerHTML = this.renderTopBarHtml(d);
    // The expiry <select> is a persistent node re-parented into the fresh
    // top-bar's #expiry-slot — only needed right after a full rebuild.
    if (window.moveExpirySelectIntoTopBar) moveExpirySelectIntoTopBar();
  } else {
    const isBear = isBearBias(d);

    const spotNum = Number(d.spot);
    let spotFlashCls = '';
    if (this._lastSpot !== null && !isNaN(spotNum) && spotNum !== this._lastSpot) {
      spotFlashCls = spotNum > this._lastSpot ? 'tick-flash-up' : 'tick-flash-down';
    }
    if (!isNaN(spotNum)) this._lastSpot = spotNum;

    const spotEl = document.getElementById('topbar-spot');
    if (spotEl) {
      spotEl.textContent = fmtI(d.spot);
      // Re-triggering the same animation class needs a reflow in between,
      // or the browser treats it as a no-op and the flash never replays.
      spotEl.className = 'spot' + (isBear ? ' bearish' : '');
      if (spotFlashCls) { void spotEl.offsetWidth; spotEl.classList.add(spotFlashCls); }
    }
    const badgeEl = document.getElementById('topbar-badge');
    if (badgeEl && d.spotChgPct !== undefined) {
      badgeEl.className = 'badge ' + (d.spotChgPct >= 0 ? 'badge-bull' : 'badge-bear');
      badgeEl.textContent = `${d.spotChgPct>=0?'▲':'▼'} ${Math.abs(d.spotChgPct).toFixed(2)}% (${d.spotChange>=0?'+':''}${Math.round(d.spotChange||0)})`;
    }
    const tickerEl = document.getElementById('index-ticker-bar');
    if (tickerEl && window.patchIndexTicker) patchIndexTicker(d);
    const dteEl = document.getElementById('dte-display');
    if (dteEl) dteEl.textContent = (d.dte||0) + 'd';
    const timeEl = document.getElementById('time-display');
    if (timeEl) timeEl.textContent = d.refreshTime || '--';
  }

  // #sec-decision wraps both the always-visible verdict card AND the
  // Decision Detail Tier-3 <details> collapsible (see renderDecisionBoxHtml's
  // single-root-wrapper comment) — this whole wrapper gets outerHTML-swapped
  // on every live tick, several times a second. Restoring the `open`
  // attribute after the swap (the previous fix here) only patches the
  // "closes itself a moment after you open it" symptom. It doesn't fix the
  // other half of the same root cause: outerHTML tears down the existing
  // <summary>/<details> nodes and builds new ones. If a tick lands between
  // mousedown and mouseup on <summary> — likely, given ticks arrive
  // several times a second — the browser cancels the click because its
  // target was removed from the DOM mid-gesture, so the toggle never fires
  // at all. That reads as "clicking Decision Detail does nothing," which is
  // the actual bug being reported, not a flicker. Fix: while the panel is
  // open, skip the destructive rebuild entirely (freeze this box's live
  // numbers — the user is actively reading the detail, not watching for
  // updates) instead of rebuilding every tick and racing to patch `open`
  // back on afterward.
  this.refreshDecisionBoxGuarded(d);
};

// Extracted from patchTopBarAndDecision so any other caller (e.g.
// DecisionBoxPanel in dashboard-panels.js) shares this exact guard instead
// of reimplementing the #sec-decision swap independently and missing the
// mousedown/mouseup click-guard fix, the way DecisionBoxPanel.refresh()
// previously did.
ChainView.prototype.refreshDecisionBoxGuarded = function(d) {
  const decEl = document.getElementById('sec-decision');
  if (decEl) {
    const detailNode = decEl.querySelector('#decision-detail-card');
    const decisionDetailWasOpen = !!(detailNode && detailNode.open);
    // decisionDetailWasOpen alone only covers the box once the browser has
    // already committed the toggle. Between mousedown and mouseup on
    // <summary> — the actual click gesture — `open` hasn't been set yet,
    // so this check reports false for that whole window and the rebuild
    // below would still fire mid-click, which is the bug this guard was
    // meant to fix in the first place. _decisionDetailPending (set/cleared
    // by _bindDecisionDetailGuard) covers exactly that gap.
    const clickInFlight = !!this._decisionDetailPending;
    if (!decisionDetailWasOpen && !clickInFlight) {
      decEl.outerHTML = this.renderDecisionBoxHtml(d, { open: decisionDetailWasOpen });
      this._bindDecisionDetailGuard();
    }
  }
};

// Attaches mousedown/mouseup (and keyboard equivalent) tracking to
// whichever #decision-detail-card <summary> currently exists in the DOM.
// Must be re-called after every outerHTML rebuild of #sec-decision, since
// that swap destroys the old node's listeners along with the node itself.
ChainView.prototype._bindDecisionDetailGuard = function() {
  const summary = document.querySelector('#decision-detail-card > summary');
  if (!summary) return;
  const details = summary.parentElement;
  const self = this;
  let safetyTimer = null;
  const setPending = () => {
    self._decisionDetailPending = true;
    // Safety net: if the gesture never completes (mouse released off the
    // element, focus lost mid keypress, etc.) `toggle` never fires and
    // clearPending below would never run, permanently wedging the guard
    // and freezing all future live updates to this box. Cap how long a
    // single gesture can hold the guard.
    clearTimeout(safetyTimer);
    safetyTimer = setTimeout(clearPending, 500);
  };
  const clearPending = () => {
    self._decisionDetailPending = false;
    clearTimeout(safetyTimer);
  };
  summary.addEventListener('mousedown', setPending);
  // Enter/Space activation on a focused <summary> has no mousedown pair —
  // cover it separately so the flag gets set for keyboard toggling too.
  summary.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') setPending();
  });
  // Clear only once the browser has actually committed the open/closed
  // change — the <details> `toggle` event — not on mouseup/click. Those
  // fire as separate event-loop tasks *before* the browser's native
  // toggle default-action runs, so a live-tick rebuild landing in that
  // gap would see the guard already cleared but `open` not yet flipped,
  // defeating the whole point of the guard (this was the residual cause
  // of the intermittent freeze even after the mousedown/mouseup fix).
  if (details) details.addEventListener('toggle', clearPending);
};

ChainView.prototype.renderDashboard = function(d) {
  _data=d;
  const atm=activeAtm(d);
  const greeksAll=d.greeks||[];
  const straddle=(d.callPremium||0)+(d.putPremium||0);
  
  const chain=getFilteredChain(d);
  // getVisibleRangeGreeks/computeNetGEX (metrics.js, IA redesign step 6)
  // — this "greeks" var is the same visible-range filter
  // _rerenderChainPanels and exec-view.js's Greeks/GEX card wiring each
  // used to redo independently. totalGEX below feeds the Greeks/Net GEX
  // Alerts card's "Live, Visible Range" figure (see chain-greeks.js's
  // step-6 correction comment) — NOT whole-chain.
  const greeks=getVisibleRangeGreeks(d, chain);
  const combinedMode=true;
  
  const maxOI=Math.max(...chain.map(r=>Math.max(r.ceOI||0,r.peOI||0)),1);
  const totalGEX=computeNetGEX(greeks);
  // Market Story card (renderExecutiveDashboard) reads d.totalGEX directly —
  // it was only ever computed as a local variable here and in renderGEX(),
  // so d.totalGEX was always undefined and the card permanently showed "—".
  d.totalGEX = totalGEX;
  const isBull=isBullBias(d);
  const isBear=isBearBias(d);
  const sigs=d.signals||[];
  
  let h='';

  // ── TOP BAR (first) ──
  // Index ticker (fixed order NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX) is now
  // rendered inline inside renderTopBarHtml() itself, so no separate patch
  // call is needed here.
  h+=this.renderTopBarHtml(d, isBear);

  // ── DECISION ENGINE PANEL ──
  h+=this.renderDecisionBoxHtml(d);

  // NOTE: Conviction Multiplier Gauge moved into Advanced Analytics
  // (advanced-analytics-view.js) — it's a derived confirm/conflict check
  // built entirely from data already shown elsewhere (FII/DII, gamma
  // flip, PCR expansion, smart money lean), not an at-a-glance fact of
  // its own, so it doesn't need to compete with the Decision Engine for
  // always-visible space. See buildAdvancedAnalyticsHtml.

  // ── ZONE: STRUCTURE & POSITIONING (IA redesign step 1, see
  // dashboard-redesign-proposal.md §2.1/§5) ── Where is positioning
  // concentrated, and what gamma regime are we in. Market Health & Story,
  // Greeks/Net GEX Alerts, Option Chain Snapshot, the dense chain table,
  // and Greeks by Moneyness all answer that same family of question, so
  // they're grouped here as one zone instead of being split across the
  // exec grid / chain-anchor / old #sec-tier2 in build order. Institutional
  // Positioning cards (Market Regime & Smart Money / Footprint Score /
  // Capital Concentration) that used to render as this grid's cards 4-6
  // moved out to the Institutional zone below — see exec-view.js's
  // renderInstitutionalGrid().
  // Divider styling (was inline `style=`, repeated identically at all
  // four zone boundaries) moved to .zone-divider/.zone-divider--* in
  // layout.css as of step 5 — see that block for the weight rationale.
  h += '<div id="zone-structure" class="zone-divider zone-divider--primary">Structure &amp; Positioning</div>';

  // ── LARGE EXECUTIVE BOXES (3-col grid: Market Health & Story | Greeks/GEX Alerts | Option Chain Snapshot) ──
  // Keep the exact markup so the live-refresh path can later compare it
  // without immediately rebuilding this entire section on its first tick.
  const executiveDashboardHtml = renderExecutiveDashboard(d);
  h += executiveDashboardHtml;

  // ── OPTIONS CHAIN ──
  // The dense Option Chain table itself lives as a static block outside
  // the dedicated Option Chain surface is synchronized separately, so this Dashboard render does not own its DOM.
  // by a dashboard rebuild — chain-anchor just marks where that block
  // gets moved to (right after the full-rebuild swap below), still within
  // the Structure & Positioning zone. The duplicate chain table + right
  // analytics panel that used to be generated directly in this template
  // have been removed: the main dense Option Chain table (see
  // ChainDenseView.buildRowsHtml) now has the same click-a-row / "▶
  // Greeks" toggle-all reveal, and its own #rightPanel
  // (RightPanelView.renderRightPanel) already carries the identical
  // Signal / OI Analytics / Volume Analytics boxes plus a Bid/Ask depth
  // box. velByStrike/velMax below are needed by the Capital Flow zone's
  // OI Flow panel further down this function.
  h += '<div id="chain-anchor"></div>';
  const velBlock=(d.oiVelocity||[]).find(b=>b.window===_velWin)||(d.oiVelocity||[])[0];
  const velByStrike={};
  if(velBlock&&velBlock.rows)velBlock.rows.forEach(vr=>{velByStrike[vr.strike]=vr;});
  const velMax=Math.max(...chain.map(r=>{const vr=velByStrike[r.strike]||{};return Math.max(Math.abs(vr.ceDOI||0),Math.abs(vr.peDOI||0));}),1);

  // ── Greeks by Moneyness (Structure & Positioning zone) ──
  // Moved here from the old #sec-tier2 row, where it was paired with
  // Institutional Activity Crux for no reason tied to either card's
  // question — Greeks by Moneyness answers "what gamma regime are we
  // in," same family as Greeks/GEX Alerts above, not an institutional
  // question. Institutional Activity Crux now renders in the
  // Institutional zone instead (see below). Single-column now instead of
  // paired 1fr/1fr, same #sec-greeks-moneyness id/markup so the
  // interactive-subtree-preserving swap in _rerenderChainPanels below
  // still finds it.
  h += `<div id="sec-tier2" class="row2" style="grid-template-columns:1fr;align-items:stretch;">
    <div id="sec-greeks-moneyness" class="section-card sc-violet" style="min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;">
      <div class="section-header"><span class="section-title"><span class="section-icon">Δ</span>Greeks by Moneyness</span></div>
      <div style="display:flex;flex-wrap:wrap;gap:16px;margin-bottom:10px;font-size:11px;color:var(--txt3);flex-shrink:0;">
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#2a78d6;"></span>Delta (call)</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#1baf7a;"></span>Gamma</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#e34948;"></span>|Theta| decay</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#eda100;"></span>Vega</span>
      </div>
      <!-- flex:1 + min-height:0 is the standard fix for Chart.js (responsive +
           maintainAspectRatio:false) inside a flex column: without min-height:0
           the flex item's default min-height:auto fights the canvas's own
           measurement and the box grows/shrinks abruptly on every render.
           Click-to-expand (openGreeksChartModal()) — same treatment as the
           Strategy Payoff / Net GEX charts, so all three chart-style cards
           behave consistently instead of only two of them being expandable. -->
      <div class="chart-expand-wrap" role="button" tabindex="0" aria-label="Expand Greeks by Moneyness chart" onclick="openGreeksChartModal()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openGreeksChartModal();}" title="Click to expand" style="cursor:zoom-in;position:relative;width:100%;flex:0.9;min-height:280px;">
        <span class="chart-expand-icon" title="Expand">⤢</span>
        <canvas id="greeksChart" role="img" aria-label="Line chart showing how delta, gamma, theta, and vega change shape from deep OTM through ATM to deep ITM for a call option, updated live from the option chain.">Delta rises steadily from OTM to ITM. Gamma, theta decay, and vega all peak at the at-the-money strike and fall off toward both deep ITM and deep OTM.</canvas>
      </div>
    </div>
  </div>`;

  // NOTE: the old always-visible #sec-iv "IV Surface" alerts section, and
  // the Tier-3 "IV vs HV / Skew" collapsible it later merged into, are
  // both gone now — the latter was removed 2026-08-01 as a duplicate of
  // Advanced Analytics' IV Rank Details card (see the block above where
  // #sec-tier3 used to be built). The full per-strike CE/PE bar table
  // still lives in its own modal (openIvSurfaceModal()), refreshed by
  // renderIvSurfaceModal().

  // ── TIER-3 SUPPORTING DETAIL ──
  // Used to hold dOI · 5/15/30m, IV vs HV / Skew, and ATM Greeks. dOI was
  // removed as redundant with the Option Chain Snapshot card's "OI Flow"
  // strip; ATM Greeks moved into the Δ Greeks / Net GEX exec card
  // (2026-08-01). IV vs HV / Skew is now removed too (2026-08-01) — it
  // duplicated Advanced Analytics' own "IV Rank Details" card
  // (advanced-analytics-view.js), which covers the same IV/HV/skew/rank
  // figures. Note the one thing that doesn't carry over: this card's two
  // alert rows (elevated skew, IV rank at an extreme) — Advanced
  // Analytics' version is explicitly a "distilled ... without its alert
  // rows" readout (see its own comment), so those two alert conditions
  // aren't surfaced anywhere on the dashboard anymore. #sec-tier3 itself
  // is gone along with its last card; nothing else rendered into it.

  // Advanced Analytics (collapsed by default) and Strategy Payoff /
  // Institutional F&O Simulator both moved to the Confirmation zone,
  // appended at the end of this function (see "ZONE: CONFIRMATION"
  // below) — they're Tier-3 exploration/scenario tooling (§3 of the IA
  // redesign proposal), not Structure & Positioning. renderSimRangeRow
  // stays defined here since the Spot/IV sliders further down (still
  // inside `if(strats.length)`) are its only remaining call site.
  const strats=d.strategies||[];

  // renderSimRangeRow() hoisted out of the (formerly) strategies-gated
  // Institutional F&O Simulator block below, since other callers
  // (previously the Vol/OI Velocity slider, now just the Spot/IV sliders
  // further down) need it before that `if(strats.length)` block runs.
  // This is the single definition; the Spot/IV sliders further down
  // (still inside `if(strats.length)`) are its only remaining caller —
  // the Vol/OI Velocity slider that used to reuse it here moved into the
  // Vol/OI Velocity modal itself (DashboardPro.html's
  // #vol-oi-velocity-modal, static markup) so it lives alongside the
  // full block-detection grid it actually controls, rather than sitting
  // on the always-visible dashboard card as a second, easy-to-miss
  // control duplicating what the header's click target now opens.
  // _simVelOverride (the value it writes) is a plain window global
  // (dashboard.js), so moving the input element doesn't change how
  // simUpdate()/_simUpdateNow() (simulator-view.js) reads it — that
  // function already tolerates the element being absent from any given
  // template pass (falls back to this.simState.vel) for exactly this
  // kind of relocation.
  function renderSimRangeRow(cfg) {
    const raw = cfg.override != null ? parseFloat(cfg.override) : cfg.base;
    const value = cfg.clamp ? Math.min(cfg.max, Math.max(cfg.min, Math.round(raw))) : raw;
    return `
        <div class="sim-ctrl-row">
          <span class="sim-ctrl-label">${cfg.label}</span>
          <input type="range" class="sim-ctrl-slider" id="sim-${cfg.id}-slider" min="${cfg.min}" max="${cfg.max}" step="${cfg.step}" value="${value}" oninput="${cfg.overrideVar}=parseFloat(this.value);simUpdate()">
          <span class="sim-ctrl-val" id="sim-${cfg.id}-val">${cfg.fmt(value)}</span>
        </div>`;
  }

  // Strategy Payoff / Institutional F&O Simulator markup is built here
  // (needs strats/spot/greeksData in scope) but appended to `h` later, in
  // the Confirmation zone — captured into a variable instead of an
  // immediate `h+=` so the build order can move without moving this
  // whole template literal. See "ZONE: CONFIRMATION" below.
  let stratSimulatorHtml = '';
  {
    // Build dropdown options
    if(_selStratIdx>=strats.length) _selStratIdx=0;
    const stratOpts = strats.map((s,i)=>`<option value="${i}"${i===_selStratIdx?' selected':''}>${s.name||('Strategy '+(i+1))}</option>`).join('');

    // == INSTITUTIONAL F&O SIMULATOR SECTION ==
  // Always inject it - uses live greeks data + simulation sliders
  {
    const simCtx = d.ctx || {};
    const greeksData = d.greeks || [];
    // Prefer the per-expiry fields (d.spot/d.atm/d.atmIV), which reflect
    // whichever expiry this connection is resolved to — d.ctx is a static
    // top-level payload field that never changes with the expiry, so
    // reading it here pinned the whole simulator to whatever expiry loaded
    // first.
    const spot = d.spot || simCtx.spot || 0;
    const atmStrike = d.atm || simCtx.atm || 0;
    const step = greeksData.length > 1 ? (greeksData[1].strike - greeksData[0].strike) : 50;
    // computeNetGEX/computeGammaFlip (metrics.js, IA redesign step 6) —
    // greeksData is d.greeks unfiltered (Live, Whole-Chain scope, same
    // as Advanced Analytics' GEX Table), used here as the simulator's
    // pre-slider baseline before simUpdate() applies the scenario
    // adjustment (see simulator-view.js's own step-6 comment).
    const totalGEX = computeNetGEX(greeksData);
    const flipRow = computeGammaFlip(greeksData, atmStrike);
    const flipStrike = flipRow ? flipRow.strike : 0;
    const vannaMultiplier = 1.0 + Math.abs(totalGEX) / 30;

    // Scenario Controls — single source of truth per slider (id, range,
    // which window-global override var it writes to, and how its value is
    // formatted). Every row is generated from renderSimRangeRow() below
    // instead of being hand-typed three times, so a control that gets
    // added/removed can't drift out of sync with its siblings — e.g. this
    // is what let the Vol/OI Velocity row get dropped from the markup
    // previously while sim-vel-val/sim-vel-slider were still expected
    // elsewhere in panels-views.js.
    const simRangeControls = [
      { id: 'spot', label: 'Spot Price',
        min: Math.round(spot*0.97), max: Math.round(spot*1.03), step: step,
        override: _simSpotOverride, overrideVar: '_simSpotOverride',
        base: spot, clamp: true, fmt: v => fmtI(Math.round(v)) },
      { id: 'iv', label: 'IV (%)',
        min: 8, max: 50, step: 0.5,
        override: _simIvOverride, overrideVar: '_simIvOverride',
        base: d.atmIV || simCtx.baseIv || 15, fmt: v => fmtN(v, 1) },
    ];

  stratSimulatorHtml+=`<div style="display:grid;grid-template-columns:${strats.length?'1fr 1fr':'1fr'};gap:16px;margin-bottom:18px;align-items:stretch;">

    ${strats.length ? `
    <!-- LEFT: Strategy Payoff -->
    <div id="sec-strats" class="section-card sc-amber" style="min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;">

      <div class="section-header"><span class="section-title"><span class="section-icon">🎯</span>Strategy Payoff</span></div>

      <!-- Dropdowns row -->
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <select id="strat-select" onchange="_selStratIdx=parseInt(this.value)||0;renderStratPayoff()" style="
          flex:1;padding:10px 14px;font-size:13px;font-weight:600;
          background:var(--bg2);color:var(--txt);
          border:1px solid var(--border);border-radius:8px;
          font-family:var(--sans);cursor:pointer;outline:none;
          appearance:none;-webkit-appearance:none;
          background-image:url('data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'12\\' height=\\'8\\' viewBox=\\'0 0 12 8\\'><path d=\\'M1 1l5 5 5-5\\' stroke=\\'%23868E96\\' stroke-width=\\'1.5\\' fill=\\'none\\' stroke-linecap=\\'round\\'></path></svg>');
          background-repeat:no-repeat;background-position:right 12px center;padding-right:34px;
        ">${stratOpts}</select>
        <select id="strat-strike-select" onchange="_selStrike=this.value?parseFloat(this.value):null;renderStratPayoff()" style="
          flex:1;padding:10px 14px;font-size:13px;font-weight:600;
          background:var(--bg2);color:var(--txt);
          border:1px solid var(--border);border-radius:8px;
          font-family:var(--sans);cursor:pointer;outline:none;
          appearance:none;-webkit-appearance:none;
          background-image:url('data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'12\\' height=\\'8\\' viewBox=\\'0 0 12 8\\'><path d=\\'M1 1l5 5 5-5\\' stroke=\\'%23868E96\\' stroke-width=\\'1.5\\' fill=\\'none\\' stroke-linecap=\\'round\\'></path></svg>');
          background-repeat:no-repeat;background-position:right 12px center;padding-right:34px;
        "><option value="">ATM Strike</option></select>
      </div>

      <!-- Metric cards row -->
      <div id="strat-metrics" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px;"></div>

      <!-- Payoff chart canvas — click to expand into a full-screen popup
           (openStratPayoffModal(), see ModalManager). The modal's own
           canvas (#strat-payoff-canvas-modal) is redrawn by the same
           renderStratPayoff() pass as this one, so it's never stale. -->
      <div class="chart-expand-wrap" role="button" tabindex="0" aria-label="Expand Strategy Payoff chart" onclick="openStratPayoffModal()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openStratPayoffModal();}" title="Click to expand" style="cursor:zoom-in;background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 14px 10px;position:relative;">
        <span class="chart-expand-icon" title="Expand">⤢</span>
        <canvas id="strat-payoff-canvas" style="width:100%;display:block;" height="280"></canvas>
      </div>

      <!-- Leg pills -->
      <div id="strat-legs-row" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;align-items:center;"></div>

    
    </div>` : ''}

    <!-- RIGHT: Institutional F&O Simulator — paired with Strategy Payoff in
         this 2-column row instead of rendering full-width below it, so the
         two "what if"-style analysis cards (payoff scenario vs. dealer
         positioning scenario) sit side by side. The GEX chart is also
         click-to-expand (openSimGexModal()), same treatment as the payoff
         chart to its left. -->
    <div id="sec-simulator" class="sim-wrap sim-amber" style="min-width:0;min-height:0;display:flex;flex-direction:column;">

      <div class="sim-header">
        <div class="sim-title">Scenario — Institutional F&amp;O Simulator</div>
        <button type="button" class="btn btn-sm" onclick="event.stopPropagation();resetScenario()" aria-label="Reset scenario inputs to current live references" title="Reset scenario inputs only; live data is not reloaded">Reset Scenario</button>
      </div>
      <div class="sim-body" style="padding:10px 14px;">

        <!-- GEX Chart — IA redesign step 2: "Scenario-Adjusted" scope tag,
             since this chart's netGEX is multiplied by the IV-ratio/vanna
             adjustment driven by the sliders above (see simUpdate()'s
             ivRatio/vannaAdj math in simulator-view.js), not the live
             totalGEX the Greeks Alerts card and GEX Table both show —
             same scope note as chain-greeks.js's buildGreeksAlertsHtml. -->
        <div class="sim-chart-area chart-expand-wrap" role="button" tabindex="0" aria-label="Expand simulated Net GEX chart" onclick="openSimGexModal()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openSimGexModal();}" title="Click to expand" style="cursor:zoom-in;padding-bottom:12px;position:relative;" id="sim-chart-container">
          <span class="chart-expand-icon" title="Expand">⤢</span>
          <div class="sim-chart-label">Net GEX Profile ($B) &#8593; <span style="text-transform:none;font-weight:500;color:var(--text-tertiary);letter-spacing:0;font-size:10px;">(Scenario-Adjusted)</span></div>
          <canvas id="sim-gex-canvas" height="180"></canvas>
          <div class="sim-annot" id="sim-annot"></div>
        </div>

        <!-- Dealer Regime bar — Dealer Bias dropdown sits at the right end
             of this same line (after the regime value), since it's the
             control that drives this readout. -->
        <div class="sim-regime-bar" id="sim-regime-bar">
          <span class="sim-regime-label">Scenario Dealer Regime</span>
          <div class="sim-regime-track" id="sim-regime-track"><div class="sim-regime-needle" id="sim-regime-needle" style="left:50%;"></div></div>
          <span class="sim-regime-val" id="sim-regime-val">Balanced</span>
          <select class="sim-dealer-sel" id="sim-dealer-sel" onchange="_simDealerOverride=this.value;simUpdate()" style="flex:none;flex-shrink:0;margin-left:8px;width:12ch;max-width:12ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
            <option value="0"${_simDealerOverride===null||_simDealerOverride==='0'?' selected':''}>Auto</option>
            <option value="1"${_simDealerOverride==='1'?' selected':''}>Long Gamma</option>
            <option value="-1"${_simDealerOverride==='-1'?' selected':''}>Short Gamma</option>
            <option value="0.5"${_simDealerOverride==='0.5'?' selected':''}>Mild Long</option>
            <option value="-0.5"${_simDealerOverride==='-0.5'?' selected':''}>Mild Short</option>
          </select>
        </div>

        <!-- Stats row -->
        <div class="sim-stats-row">
          <div class="sim-stat">
            <div class="sim-stat-label">Scenario Net GEX ($B)</div>
            <div class="sim-stat-val" id="sim-stat-gex" style="color:${totalGEX>=0?'var(--blue)':'var(--red)'};">${fmtN(totalGEX,2)}</div>
            <div class="sim-stat-sub">${totalGEX>=0?'Scenario: long gamma (dampens)':'Scenario: short gamma (amplifies)'}</div>
          </div>
          <div class="sim-stat">
            <div class="sim-stat-label">Scenario Vanna Multiplier</div>
            <div class="sim-stat-val" id="sim-stat-vanna" style="color:var(--amber);">${fmtN(vannaMultiplier,2)}</div>
            <div class="sim-stat-sub">IV-flow amplifier</div>
          </div>
          <div class="sim-stat">
            <div class="sim-stat-label">Scenario-Adjusted Gamma Flip</div>
            <div class="sim-stat-val" id="sim-stat-flip" style="color:var(--red);">${flipStrike?fmtI(flipStrike):'--'}</div>
            <div class="sim-stat-sub">Short &rarr; Long GEX</div>
          </div>
        </div>

        <!-- Simulation Controls -->
        <div style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Scenario Inputs · Live references remain unchanged</div>
        <div class="sim-controls" style="grid-template-columns:1fr;">
          ${simRangeControls.map(renderSimRangeRow).join('')}
        </div>

      </div>

    </div>

  </div>

  `;
  }
  }

  // ── OI FLOW SECTION ──
  // Vol/OI Velocity by Strike (Block Detection) + OI Flow Snapshot +
  // Institutional Activity Crux, wrapped together in one shared
  // #oi-flow-section container so Vol/OI Velocity reads as part of OI
  // Flow (not a separate card sitting above it). It used to live inside
  // #sdt-panel gated behind `if(strats.length)` above — a strategies-only
  // gate that never made sense for it (it only reads live greeks + the
  // vel scenario slider, nothing strategy-specific), so days with no open
  // strategies lost the whole block-detection read; this wrapper renders
  // unconditionally, same as OI Flow itself always has.
  //
  // Vol/OI Velocity keeps its own #sdt-panel identity as a direct child
  // here (rather than being folded into oi-flow-summary-card's own
  // markup) because oi-flow-summary-card is outerHTML-diffed on every WS
  // tick (patchOuterHtmlIfChanged below) whenever its block-print count
  // changes — near-constant. That diff-and-swap has no drag-guard for a
  // <input type=range> (bindCardClickGuard only tracks buttons/[onclick]),
  // so a slider nested inside that card would get torn out from under an
  // in-progress drag on almost every tick — the exact flicker/lost-
  // interaction bug #sdt-panel's own old/fresh node-swap below already
  // exists to prevent. Keeping it a sibling child of this same wrapper
  // gets it visually and structurally inside OI Flow while keeping that
  // protection intact. renderSimRangeRow()/velControl are hoisted above
  // the (former) `if(strats.length)` gate — see that comment — so this is
  // still their only call site, no second copy of either.
  //
  // #sdt-panel and #oi-flow-summary-card are wrapped in a shared
  // .oic-merged-card container (2026-08-02) so the two read as ONE card
  // (Vol/OI Velocity stacked on top of OI Flow Snapshot, divided by a
  // single hairline) instead of two separately-bordered boxes stacked
  // with a gap between them. #sdt-panel drops its old .sim-wrap chrome
  // (own border/background/shadow) in favor of .oic-merged-vel, which is
  // chromeless and just borrows the outer wrapper's card surface;
  // .oic-merged-card also strips #oi-flow-summary-card's own .oic-card
  // border/background/shadow via a descendant-selector override in
  // panels.css so it seats flush under the velocity panel — none of that
  // touches buildOiFlowSummaryHtml() itself (still the same markup,
  // still the same outerHTML-patch target), only how it looks once
  // nested here.
  //
  // ── ZONE: CAPITAL FLOW ──
  // Where is money moving intraday — OI Flow + FII/DII, a coherent
  // "where is money moving" pair instead of OI Flow sitting next to an
  // institutional-positioning card. Institutional Activity Crux (this
  // grid's second column previously) now lives in its own Institutional
  // zone below, alongside Market Regime & Smart Money / Institutional
  // Footprint Score / Capital Concentration — see "ZONE: INSTITUTIONAL"
  // further down.
  h += `<div id="oi-flow-section">

  <div id="zone-capital-flow" class="zone-divider zone-divider--primary">Capital Flow</div>
  <div class="capital-flow-grid">

    <div class="oic-merged-card">
      <div id="sdt-panel" class="oic-merged-vel">
        <button class="section-header nav-card-header" onclick="openVolOiVelocityModal()"
           aria-label="Open Vol/OI Velocity by Strike — view block-detection chart" title="Open the block-detection chart">
          <span class="section-title nav-card-header-label"><span class="section-icon">⚡</span>Vol/OI Velocity by Strike <span style="text-transform:none;font-weight:500;color:var(--text-tertiary);letter-spacing:0;">(Block Detection)</span></span>
          <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
        </button>
        <!-- The Vol/OI Velocity slider that used to sit here has moved
             into the Vol/OI Velocity modal itself (opened by the header
             above) — see the renderSimRangeRow() comment near the top of
             this function for why. The dashboard card is now just the
             header + block-prints readout below; dragging the velocity
             slider happens inside the modal, next to the grid it
             actually controls. -->

        <!-- The chart-expand-wrap box that used to sit here (zoom-in
             cursor, expand icon, opened the same modal on click) has been
             removed outright rather than left as an empty bordered box —
             once its click target and icon were stripped (the header
             above is now the single click target for this card), the
             leftover background/border/radius box had no content and no
             function, just a visual remnant of the old click affordance.
             Block-print summary line ("N block prints flagged •
             strongest STRIKE SIDE") lives at #oi-flow-block-summary
             inside the OI Flow Snapshot card right below, written by
             simRenderVolGrid() (simulator-view.js) every tick/scenario-
             slider move. -->

        <!-- Strike Detail table itself lives only in the Strike Detail
             Report modal now (opened via the "📄 Strike Detail Report →"
             button on the Institutional Activity Crux card below) — the
             inline collapse/expand version that used to render here was a
             duplicate and has been removed. simRenderTable() (simulator-
             view.js) still computes the rows/stats every tick and writes
             them directly into the modal's #sdt-rows/#sdt-stat-* elements;
             no inline element needed here to do so. -->
      </div>
      ${buildOiFlowSummaryHtml(chain, atm, velByStrike, d.oiVelocity)}
    </div>

    ${buildFiiDiiSummaryCard(d)}
  </div>

  </div>`;

  // ── ZONE: INSTITUTIONAL (IA redesign step 1) ──
  // Who is moving the market, and where. Market Regime & Smart Money /
  // Institutional Footprint Score / Capital Concentration used to render
  // as cards 4-6 of the Structure exec-grid (see exec-view.js's
  // renderExecutiveDashboard — split out into renderInstitutionalGrid());
  // Institutional Activity Crux used to pair with Greeks by Moneyness in
  // this same #sec-tier2 slot for no reason tied to either card's
  // question (Greeks by Moneyness moved to Structure & Positioning
  // above). All four institutional-intent cards now sit together here.
  // Smart Money Ranking now lives in its own Probability card
  // (probability-view.js, Confirmation zone) as of step 7's second pass —
  // not here. Conviction Gauge's Smart Money Lean pillar stays inside
  // Advanced Analytics for now; it's a derived input to that card's
  // verdict, not a standalone ranking, so it doesn't map to Probability
  // the way the ranking itself did.
  h += '<div id="zone-institutional" class="zone-divider zone-divider--secondary">Institutional</div>';
  h += app.exec.renderInstitutionalGrid(d);
  h += app.exec.buildInstitutionalActivitySummaryCard(d);

  // ── ZONE: CONFIRMATION ──
  // Supporting evidence that validates or challenges the Decision
  // Engine's call, opened after the Tier-1 verdict rather than competing
  // with it for always-visible space (§3 of the IA redesign proposal).
  // Volatility (IV Rank details), Probability (Smart Money Ranking), and
  // Scenario Analysis (Scenario P&L) are the three sub-cards pulled out
  // of Advanced Analytics into their own purpose-specific cards so far
  // (roadmap step 7, dashboard-redesign-proposal.md §2.3 — see
  // volatility-view.js / probability-view.js / scenario-analysis-view.js);
  // mounted first since they now stand alone rather than living inside
  // the Advanced Analytics grid. The rest of Advanced Analytics (GEX
  // table / OI Velocity / per-strike Greeks / Capital Confirmation /
  // Futures-Options Divergence) stays one collapsible — none of it maps
  // cleanly to Volatility/Probability/Scenario Analysis, and the fourth
  // named destination (Cross-Market) has no candidate content yet, so
  // this is likely close to Advanced Analytics' final shape. Strategy
  // Payoff / Institutional F&O Simulator sits right after Scenario
  // Analysis: both are "what if" scenario tools (Tier 3) answering
  // adjacent questions (single-straddle expiry payoff vs. multi-leg
  // strategy payoff / GEX-scenario exposure) — built earlier
  // (stratSimulatorHtml, needs strats/spot/greeksData in scope) but
  // appended here so build order matches display order.
  h += '<div id="zone-confirmation" class="zone-divider zone-divider--tertiary">Confirmation</div>';
  h += this.buildVolatilityHtml(d);
  h += this.buildProbabilityHtml(d);
  h += this.buildScenarioAnalysisHtml(d);
  h += this.buildAdvancedAnalyticsHtml(d);
  // Collapsed by default (no `open` attribute, matching Advanced Analytics'
  // own <details class="card"> right above) — closes the gap where this
  // block previously rendered as a plain, always-open <div>, undermining
  // §3's "Confirmation collapses by default" for half the zone even though
  // Advanced Analytics itself already got this right. Guarded on
  // The simulator is independent of strategy availability; only the
  // Strategy Payoff half is omitted when no strategy list exists.
  if (stratSimulatorHtml) {
    h += `<details class="card" id="strategy-simulator-card">
    <summary>
      <div class="card-head"><span class="ic">🧪</span>${strats.length?'Strategy Payoff &amp; ':''}Institutional F&amp;O Simulator<span class="fill"></span></div>
      <span class="chev">▶</span>
    </summary>
    <div class="detail-body">
      ${stratSimulatorHtml}
    </div>
  </details>`;
  }


  // Risk Dashboard (Trade grade / IV regime / Trap warning / key levels)
  // used to be its own standalone section-card here. Removed — that
  // content now folds into the Tier-1 verdict card's second row (see
  // renderDecisionBoxHtml in chain-template.js), so it's seen immediately
  // instead of requiring a scroll past OI Flow/IV Surface/Simulator to
  // reach it. keyLevels (R1/S1/R2/S2) still lives in the Decision Detail
  // collapsible's S&R Levels grid, unchanged.



  // Detach the chart canvases before the full rebuild so their last-drawn
  // frame stays on screen instead of flashing blank while charts redraw.
  const _oldPayoffCanvas = document.getElementById('strat-payoff-canvas');
  const _oldGexCanvas = document.getElementById('sim-gex-canvas');

  // ── FLICKER FIX: preserve the whole Strategy Payoff / Institutional
  // Simulator / Greeks-by-Moneyness subtrees across live ticks ──
  // Every WS tick runs this full rebuild, which was destroying and
  // recreating ALL of their DOM every time — 4 range sliders, 2 <select>
  // dropdowns, and the vol-grid/strike-table, not just the two canvases
  // handled above. That churn is what read as "heavy flicker" on these
  // two sections specifically (native form controls repaint far more
  // noticeably than plain text does). Their actual numbers are already
  // refreshed afterward by renderStratPayoff()/simInit() without touching
  // these nodes, so it's safe to keep the old nodes as-is whenever the
  // strategy list itself hasn't structurally changed (same names/count) —
  // only rebuild them fresh when the strategy list actually changes.
  const dashEl = $i('dashboard');
  const _stratsSig = (d.strategies||[]).map(s=>s.name||'').join('|');
  const _keepInteractiveSubtrees = dashEl && dashEl.dataset.stratsSig === _stratsSig;
  const _oldStratsSection    = _keepInteractiveSubtrees ? document.getElementById('sec-strats') : null;
  const _oldGreeksMoneySect  = _keepInteractiveSubtrees ? document.getElementById('sec-greeks-moneyness') : null;
  const _oldSimSection       = _keepInteractiveSubtrees ? document.getElementById('sec-simulator') : null;
  const _oldSimDetailSection = _keepInteractiveSubtrees ? document.getElementById('sdt-panel') : null;

  // The dense Option Chain block is never part of the `h` string above (it
  // only contains a `#chain-anchor` placeholder for it) — it's always the
  // same persistent DOM node, moved into place after every rebuild rather
  // than rebuilt, so its scroll position, click-to-reveal state, and live
  // data binding survive full rebuilds unconditionally (not just when the
  // strategy list is unchanged).
  const _chainSection    = document.getElementById('sec-chain');

  const _prevScrollY = window.scrollY;
  // Full rebuild replaces the chain table too, which would otherwise reset
  // its internal scroll on every live tick — capture it first so it can be
  // restored below unless we're deliberately re-centering on ATM.
  const _prevChainEl = $i('chain-scroll');
  const _prevChainScrollTop = _prevChainEl ? _prevChainEl.scrollTop : null;

  if (this._decisionDetailPending
      || (typeof isCardClickPending === 'function'
          && (isCardClickPending('chainSummary')
              || isCardClickPending('fiiDiiSummary')
              || isCardClickPending('instActivity')))) {
    setTimeout(() => this.renderDashboard(d), 60);
    return;
  }
  
  $i('dashboard').innerHTML = h;
  if(dashEl) dashEl.dataset.stratsSig = _stratsSig;
  const initialExecWrap = document.getElementById('exec-section-wrap');
  if(initialExecWrap) initialExecWrap.dataset.lastHtml = executiveDashboardHtml;
  if (window.moveExpirySelectIntoTopBar) moveExpirySelectIntoTopBar();
  // Top-bar content (VIX pill, badges, etc.) can change its rendered
  // height on any tick, so re-measure the sticky stack after each rebuild.
  requestAnimationFrame(updateStickyOffsets);
  // Full rebuild replaces every node, which resets scroll position; put it
  // back so a live tick doesn't yank the page while someone's reading it.
  window.scrollTo(0, _prevScrollY);
  requestAnimationFrame(app.chain.sizeAndScrollChain.bind(app.chain, _prevChainScrollTop));

  // Swap the whole old subtrees back in first (covers their canvases too),
  // then fall back to the narrower canvas-only swap below for whichever
  // ones weren't preserved (e.g. the very first render, or a tick where
  // the strategy list actually changed).
  if(_oldStratsSection){
    const fresh = document.getElementById('sec-strats');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldStratsSection, fresh);
  }
  if(_oldGreeksMoneySect){
    const fresh = document.getElementById('sec-greeks-moneyness');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldGreeksMoneySect, fresh);
  }
  if(_oldSimSection){
    const fresh = document.getElementById('sec-simulator');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldSimSection, fresh);
  }
  if(_oldSimDetailSection){
    const fresh = document.getElementById('sdt-panel');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldSimDetailSection, fresh);
  }

  // Drop the dense Option Chain block into the anchor point between
  // Decision/Executive boxes and OI Flow. Runs on every full rebuild
  // (not gated by _keepInteractiveSubtrees) since the chain block isn't
  // regenerated by this template at all — only relocated.
  // _chainRightPanel already lives INSIDE _chainSection — it's the second
  // Dashboard-local layout only; the dedicated Option Chain owns its own
  // DashboardPro.html). It used to also be independently re-inserted as a
  // sibling of _chainSection right after moving _chainSection itself,
  // which (a) pulled it out of the 1fr/220px grid it belongs in, making it
  // render as a detached-looking floating box instead of sitting next to
  // the table, and (b) on the following render could hand insertBefore a
  // node whose new position was already inside its own subtree, throwing
  // "the new child element contains the parent" and aborting the entire
  // render (visible as the loader/error screen appearing over stale data).
  // Moving _chainSection alone already carries rightPanel along with it,
  // so the separate move is unnecessary as well as unsafe — removed.
  const _chainAnchor = document.getElementById('chain-anchor');
  if(_chainAnchor && _chainSection && !_chainSection.contains(_chainAnchor)){
    _chainAnchor.parentNode.insertBefore(_chainSection, _chainAnchor);
    _chainAnchor.remove();
  } else if(_chainAnchor){
    _chainAnchor.remove();
  }

  // Swap the freshly-created (blank) canvases out for the old ones so
  // there's no visible flash; renderStratPayoff()/simUpdate() redraw onto
  // them normally a moment later. (No-ops when the whole-subtree swap
  // above already restored them.)
  if(_oldPayoffCanvas){
    const freshPayoffCanvas = document.getElementById('strat-payoff-canvas');
    if(freshPayoffCanvas && freshPayoffCanvas.parentNode) freshPayoffCanvas.parentNode.replaceChild(_oldPayoffCanvas, freshPayoffCanvas);
  }
  if(_oldGexCanvas){
    const freshGexCanvas = document.getElementById('sim-gex-canvas');
    if(freshGexCanvas && freshGexCanvas.parentNode) freshGexCanvas.parentNode.replaceChild(_oldGexCanvas, freshGexCanvas);
  }
  
  // ── POST-RENDER ──
  renderVelocity(_velWin);
  renderGreeksGex(_grkView);
  // BUGFIX: this call was documented (see renderIvSurfaceModal's own
  // comment and openIvSurfaceModal's) as already wired into every
  // render/tick, but it was never actually added anywhere except inside
  // openIvSurfaceModal() itself — so the modal only ever painted once, at
  // the moment it was opened. Left open across a range switch, expiry
  // change, or live tick, it just kept showing whatever chain/ATM was
  // active when you clicked it. Added here (full rebuild) and in
  // _rerenderChainPanels below (incremental refresh, which is what
  // switchChainRange actually calls) so both paths keep it current
  // exactly like Greeks/GEX and OI Velocity already are.
  this.renderIvSurfaceModal();
  this._bindDecisionDetailGuard();
  // Same click-guard the incremental per-tick refresh binds after each of
  // its own outerHTML swaps (see chain-renderer.js's chainSummaryEl /
  // instActivityEl blocks) — bound here too so the very first tick after
  // this full rebuild is already protected, not just ticks after the
  // first incremental swap.
  bindCardClickGuard(document.getElementById('chain-summary-card'), 'chainSummary');
  bindCardClickGuard(document.getElementById('fiidii-summary-card'), 'fiiDiiSummary');
  bindCardClickGuard(document.getElementById('inst-activity-summary-card'), 'instActivity');
  setTimeout(function(){
    simInit();
    if(app.strikeDetail) app.strikeDetail.refresh();
  },50);
  _afterRenderStratPayoff();
  
  
  if(_greeksVisible){
    document.querySelectorAll('[id^="grk-row-"]').forEach(el=>{el.style.display='';});
    const icon=$i('grk-toggle-icon');
    const btn=$i('grk-toggle-btn');
    if(icon)icon.textContent='▼';
    if(btn)btn.classList.add('on');
  }
  
  updateStickyNav(d);
  
  // Update range nav expiry info
  const expDisplay = document.getElementById('expiry-display');
  const dteDisplay = document.getElementById('dte-display');
  const timeDisplay = document.getElementById('time-display');
  if(expDisplay) expDisplay.textContent = d.expiry || '--';
  if(dteDisplay) dteDisplay.textContent = (d.dte||0) + 'd';
  if(timeDisplay) timeDisplay.textContent = d.refreshTime || '--';
};

ChainView.prototype.sizeAndScrollChain = function(prevScrollTop) {
  const wrap=$i('chain-scroll');
  if(!wrap)return;
  const thead=wrap.querySelector('thead');
  const sampleRow=wrap.querySelector('tbody tr');
  if(sampleRow){
    const rowH=sampleRow.getBoundingClientRect().height||32;
    const theadH=thead?thead.getBoundingClientRect().height:0;
    // Viewport always shows 7 strike-rows regardless of which ATM range
    // (±5/±7/±10/All etc.) is currently selected in the range filter — the
    // range only controls how many total strikes get loaded into the
    // scrollable list; this fixed height is what makes the rest scrollable
    // by sliding up/down within the box instead of growing the page.
    wrap.style.maxHeight=Math.round(theadH+rowH*7)+'px';
  }
  if(_centerChainOnATM){
    const atmRow=$i('chain-row-atm');
    if(atmRow){
      const target=atmRow.offsetTop-(wrap.clientHeight/2)+(atmRow.clientHeight/2);
      wrap.scrollTop=Math.max(target,0);
    }
    _centerChainOnATM=false;
  }else if(prevScrollTop!=null){
    wrap.scrollTop=prevScrollTop;
  }
};

ChainView.prototype.renderVelocity = function(win) {
  const el=$i('vel-content');if(!el||!_data)return;
  const vel=_data.oiVelocity;
  if(!vel||!vel.length){el.innerHTML='<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No OI velocity data.</div>';return;}
  const block=vel.find(b=>b.window===win)||vel[0];
  const chainStrikes=new Set(getFilteredChain(_data).map(c=>c.strike));
  const rows=(block.rows||[]).filter(r=>chainStrikes.size===0||chainStrikes.has(r.strike));
  if(!rows.length){el.innerHTML=`<div style="font-size:12px;color:var(--txt3);padding:8px 0;">No data for ${win}-min window.</div>`;return;}
  const maxAbs=Math.max(...rows.map(r=>Math.max(Math.abs(r.ceDOI||0),Math.abs(r.peDOI||0))),1);
  const atm=activeAtm(_data);
  let h=`<table class="t"><thead><tr>
    <th style="text-align:center;width:62px;">Strike</th>
    <th style="width:56px;">CE now</th><th style="width:90px;">CE ΔOI</th><th style="width:44px;">CE LTP</th>
    <th style="width:56px;">PE now</th><th style="width:90px;">PE ΔOI</th><th style="width:44px;">PE LTP</th>
    <th style="text-align:left;width:96px;">Signal</th>
  </tr></thead><tbody>`;
  rows.forEach(r=>{
    const ia=r.strike===atm;const sc=ia?' atm-sc':'sc';
    function velDOICell(v,maxAbs){
      const pct=maxAbs>0?Math.min(Math.abs(v)/maxAbs*24,24):0;
      const bar=v>=0?`<div style="width:${pct.toFixed(0)}px;background:var(--green);border-radius:2px;height:8px;display:inline-block;flex-shrink:1;max-width:24px;"></div>`:`<div style="width:${pct.toFixed(0)}px;background:var(--red);border-radius:2px;height:8px;display:inline-block;flex-shrink:1;max-width:24px;"></div>`;
      return `<div style="display:flex;align-items:center;gap:4px;justify-content:flex-end;overflow:hidden;min-width:0;">${bar}<span style="color:${sClr(v)};font-size:10px;font-family:var(--mono);white-space:nowrap;flex-shrink:0;">${v>=0?'+':''}${fmtK(v)}</span></div>`;
    }
    h+=`<tr>
      <td class="${sc}">${fmtI(r.strike)}${ia?' ★':''}</td>
      <td style="font-size:10px;color:var(--txt2);">${fmtK(r.ceNow)}</td>
      <td>${velDOICell(r.ceDOI,maxAbs)}</td>
      <td style="font-weight:600;font-family:var(--mono);">${fmtN(r.ceLTP,1)}</td>
      <td style="font-size:10px;color:var(--txt2);">${fmtK(r.peNow)}</td>
      <td>${velDOICell(r.peDOI,maxAbs)}</td>
      <td style="font-weight:600;font-family:var(--mono);">${fmtN(r.peLTP,1)}</td>
      <td style="text-align:left;"><span class="sp sp-n">${r.signal||'—'}</span></td>
    </tr>`;
  });
  const netCE=rows.reduce((s,r)=>s+(r.ceDOI||0),0);
  const netPE=rows.reduce((s,r)=>s+(r.peDOI||0),0);
  h+=`</tbody></table>
    <div class="section-footer">
      <span>CE builds: <strong style="color:var(--red);">${rows.filter(r=>r.ceDOI>0).length}/${rows.length}</strong></span>
      <span>PE builds: <strong style="color:var(--green);">${rows.filter(r=>r.peDOI>0).length}/${rows.length}</strong></span>
      <span>Net CE ΔOI: <strong style="color:${sClr(netCE)}">${netCE>=0?'+':''}${fmtK(netCE)}</strong></span>
      <span>Net PE ΔOI: <strong style="color:${sClr(netPE)}">${netPE>=0?'+':''}${fmtK(netPE)}</strong></span>
      <span>Window: <strong>${win} min</strong></span>
    </div>`;
  el.innerHTML=h;
};

  // Writes the full IV surface (buildIvSurfaceHtml above) into the modal's
  // static content div. Reads _data itself (same pattern as
  // renderGreeksGex(view) below) so it can be called with no args from
  // renderDashboard's post-render block, live ticks, and expiry switches.
ChainView.prototype.renderIvSurfaceModal = function() {
  const el = $i('iv-surface-content');
  if(!el || !_data) return;
  const chain = getFilteredChain(_data);
  const atm = activeAtm(_data);
  el.innerHTML = this.buildIvSurfaceHtml(_data, chain, atm);
};

  ChainView.prototype.onExpiryChange = function(selectedExpiry) {
  if(!_data || !selectedExpiry) {
    return;
  }
  const activeExpiry = _data.expiry || '';
  const _same = activeExpiry && (
    selectedExpiry === activeExpiry
    || (typeof parseExpiryDate === "function" && parseExpiryDate(selectedExpiry) === parseExpiryDate(activeExpiry))
  );
  if(_same) return; // already showing this expiry, nothing to do

  const sel = (typeof getExpirySelectNode === 'function') ? getExpirySelectNode() : null;
  if (sel) {
    sel.value = selectedExpiry;
    sel.dataset.pendingExpiry = selectedExpiry;
    sel.dataset.pendingExpiryTime = Date.now().toString();
  }

  const base = (_wsUrl || '').split('?')[0];
  const params = new URLSearchParams((_wsUrl || '').split('?')[1] || '');
  params.set('expiry', selectedExpiry);
  const newUrl = `${base}?${params.toString()}`;
  connectWebSocket(newUrl);
};

ChainView.prototype._rerenderChainPanels = function() {
  if(!_data) return;

  const chain          = getFilteredChain(_data);
  const chainStrikeSet = new Set(chain.map(r=>r.strike)); // still needed below (vol/OI velocity totals)
  const atm            = activeAtm(_data);
  const greeksAll      = _data.greeks || [];
  // getVisibleRangeGreeks (metrics.js, IA redesign step 6) — same
  // visible-range filter as renderDashboard's `greeks`.
  const greeks         = getVisibleRangeGreeks(_data, chain);
  const velBlock       = (_data.oiVelocity||[]).find(b=>b.window===_velWin)||(_data.oiVelocity||[])[0];
  const velByStrike    = {};
  if(velBlock&&velBlock.rows) velBlock.rows.forEach(vr=>{velByStrike[vr.strike]=vr;});
  const velMax         = Math.max(...chain.map(r=>{const vr=velByStrike[r.strike]||{};return Math.max(Math.abs(vr.ceDOI||0),Math.abs(vr.peDOI||0));}),1);
  const oiAnnot        = (_data.decision&&_data.decision.oiAnnotations)||{};
  const maxOI          = Math.max(...chain.map(r=>Math.max(r.ceOI||0,r.peOI||0)),1);

  // ── 1. Chain table body ───────────────────────────────────────────────────
  const chainEl = document.getElementById('chain-body');
  if(chainEl){
    let rows='';
    chain.forEach(r=>{
      const ia=r.atm||r.strike===atm; const ac=ia?' atm':''; const acs=ia?' atm-sc':'sc';
      const g=greeks.find(x=>x.strike===r.strike)||{};
      const sk=r.strike;
      const vr=velByStrike[sk]||{};
      const ceVelDOI=vr.ceDOI!=null?vr.ceDOI:0;
      const peVelDOI=vr.peDOI!=null?vr.peDOI:0;
      const cs=combinedSignal(r.ceSignal,r.peSignal);
      const annot=oiAnnot[String(sk)]||{};
      const rowTitle=annot.ce||annot.pe?`CE: ${annot.ce||'—'} | PE: ${annot.pe||'—'}`:'Click to show/hide Greeks';
      rows+=`<tr${ia?' id="chain-row-atm"':''} style="cursor:pointer;" tabindex="0" aria-label="Strike ${sk}; press Enter for Greeks" onclick="toggleGreekRow(${sk})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleGreekRow(${sk})}" title="${rowTitle}">`;
      rows+=`<td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.ceVol)}</td>
        <td class="${ac}">${velMiniCell(ceVelDOI,velMax,ceOiChgClr(ceVelDOI))}</td>
        <td class="${ac} pt-ltp-click" role="button" tabindex="0" aria-label="Trade ${sk} CE" style="font-weight:600;font-family:var(--mono);" onclick="event.stopPropagation();ptOpenQuickOrder(event,${sk},'CE',${r.ceLTP!=null?r.ceLTP:'null'})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();ptOpenQuickOrder(event,${sk},'CE',${r.ceLTP!=null?r.ceLTP:'null'})}" title="Click to trade this strike">${fmtN(r.ceLTP,1)}</td>
        <td class="${ac}" style="color:${ceOiChgClr(r.ceDOI)};font-size:10px;">${(r.ceDOI||0)>=0?'+':''}${fmtK(r.ceDOI)}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.ceOI)}</td>
        <td class="${acs}" style="white-space:nowrap;line-height:1.15;">${fmtI(r.strike)}${ia?' ★':''}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.peOI)}</td>
        <td class="${ac}" style="color:${sClr(r.peDOI)};font-size:10px;">${(r.peDOI||0)>=0?'+':''}${fmtK(r.peDOI)}</td>
        <td class="${ac} pt-ltp-click" role="button" tabindex="0" aria-label="Trade ${sk} PE" style="font-weight:600;font-family:var(--mono);" onclick="event.stopPropagation();ptOpenQuickOrder(event,${sk},'PE',${r.peLTP!=null?r.peLTP:'null'})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();ptOpenQuickOrder(event,${sk},'PE',${r.peLTP!=null?r.peLTP:'null'})}" title="Click to trade this strike">${fmtN(r.peLTP,1)}</td>
        <td class="${ac}">${velMiniCell(peVelDOI,velMax,sClr(peVelDOI))}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.peVol)}</td>
        <td style="text-align:right;padding-right:10px;"><span class="sp ${cs.cls}">${cs.label}</span></td>
      </tr>
      ${g.cDelta!=null?`<tr id="grk-row-${sk}" class="grk-row" style="display:none;">
        <td colspan="12" style="text-align:left;padding:4px 12px;white-space:nowrap;font-size:10px;color:var(--txt3);">
          <span style="display:inline-block;min-width:140px;">CΔ <strong style="color:var(--blue);">${fmtN(g.cDelta,3)}</strong> &nbsp;Γ×10⁴ <strong style="color:var(--amber);">${fmtN(g.cGamma,3)}</strong> &nbsp;Θ <strong style="color:var(--red);">${fmtN(g.cTheta,2)}</strong> &nbsp;Vega <strong style="color:var(--blue);">${fmtN(g.cVega,2)}</strong></span>
          <span style="display:inline-block;min-width:80px;margin-left:8px;">GEX <strong style="color:${sClr(g.netGEX||0)};">${fmtN(g.netGEX,3)}B</strong></span>
          <span style="display:inline-block;min-width:110px;margin-left:30px;">CE IV <strong style="color:var(--red);">${fmtN(r.ceIV,2)}%</strong> &nbsp;PE IV <strong style="color:var(--green);">${fmtN(r.peIV,2)}%</strong></span>
          <span style="display:inline-block;min-width:80px;margin-left:20px;">PΔ <strong style="color:var(--red);">${fmtN(g.pDelta,3)}</strong></span>
          <span style="display:inline-block;min-width:160px;margin-left:20px;">CE Signal <strong class="sp ${spClass(r.ceSignal)}">${r.ceSignal||'—'}</strong></span>
          <span style="display:inline-block;min-width:160px;margin-left:10px;">PE Signal <strong class="sp ${spClass(r.peSignal)}">${r.peSignal||'—'}</strong></span>
        </td>
      </tr>`:''}`;
    });
    chainEl.innerHTML = rows;
    if(_greeksVisible) document.querySelectorAll('[id^="grk-row-"]').forEach(el=>{el.style.display='';});
    _centerChainOnATM=true; // expiry just changed — snap the viewport back to ATM ±5
    requestAnimationFrame(()=>app.chain.sizeAndScrollChain(null));
  }

  // ── 2. DTE pill ──────────────────────────────────────────────────────────
  const dteEl = document.getElementById('dte-display');
  if(dteEl){
    const dte = _data.dte || 0;
    dteEl.textContent = dte+'d';
    dteEl.style.color = dte<=1?'var(--red)':dte<=3?'var(--amber)':'var(--amber)';
  }

  // ── 3. Right analytics panel ──────────────────────────────────────────────
  const rpEl = document.querySelector('.chain-right-panel');
  if(rpEl){
    const totCeOI  = chain.reduce((s,r)=>s+(r.ceOI||0),0);
    const totPeOI  = chain.reduce((s,r)=>s+(r.peOI||0),0);
    const totCeDOI = chain.reduce((s,r)=>s+(r.ceChgOI||0),0);
    const totPeDOI = chain.reduce((s,r)=>s+(r.peChgOI||0),0);
    const velBlockRP=((_data.oiVelocity||[]).find(b=>b.window===_velWin)||(_data.oiVelocity||[])[0]);
    const totCeVel=(velBlockRP&&velBlockRP.rows||[]).filter(r=>chainStrikeSet.has(r.strike)).reduce((s,r)=>s+(r.ceDOI||0),0);
    const totPeVel=(velBlockRP&&velBlockRP.rows||[]).filter(r=>chainStrikeSet.has(r.strike)).reduce((s,r)=>s+(r.peDOI||0),0);
    const maxDOIrp=Math.max(Math.abs(totCeDOI),Math.abs(totPeDOI),1);
    const maxVelrp=Math.max(Math.abs(totCeVel),Math.abs(totPeVel),1);
    function rpBar(v,max,clr){const w=Math.max(Math.round(Math.abs(v)/max*72),2);return `<div class="crp-spark-wrap"><div class="crp-spark" style="width:${w}px;background:${clr};"></div><span style="font-size:9px;font-family:var(--mono);color:${clr};">${fmtK(v)}</span></div>`;}
    const bullStrikes=chain.filter(r=>{const cs=combinedSignal(r.ceSignal,r.peSignal);return cs.cls==='sp-strongbull'||cs.cls==='sp-bull';}).length;
    const bearStrikes=chain.filter(r=>{const cs=combinedSignal(r.ceSignal,r.peSignal);return cs.cls==='sp-strongbear'||cs.cls==='sp-bear';}).length;
    const aggBias=bullStrikes>bearStrikes?{label:'Bullish',cls:'sp-bull'}:bearStrikes>bullStrikes?{label:'Bearish',cls:'sp-bear'}:{label:'Mixed',cls:'sp-mixed'};
    const panelPCR=totCeOI>0?(totPeOI/totCeOI).toFixed(2):'—';
    const pcrColor=parseFloat(panelPCR)>1?'var(--green)':parseFloat(panelPCR)<0.8?'var(--red)':'var(--amber)';
    const netOI=totPeOI-totCeOI; const netDOI=totPeDOI-totCeDOI; const netVel=totPeVel-totCeVel;
    const netAbsMax=Math.max(Math.abs(netOI),Math.abs(netDOI),Math.abs(netVel),1);
    const arpBarW=(v,max)=>Math.max(Math.round(Math.abs(v)/max*72),3);
    const totCeVolChg=chain.reduce((s,r)=>s+(r.ceVolChg||0),0);
    const totPeVolChg=chain.reduce((s,r)=>s+(r.peVolChg||0),0);
    const maxVolChg=Math.max(Math.abs(totCeVolChg),Math.abs(totPeVolChg),1);
    // ── VOL VEL FIX (v2) ──
    // v1 diffed against the *previous render*, which fires almost every WS
    // tick (multiple times a second) — not a "(${_velWin}m)" velocity at
    // all, just a sub-second delta. That's why it was spiking from near-zero
    // to large and back on every tick instead of showing a stable 5-minute
    // trend. Fix: keep a timestamped history buffer and diff each strike's
    // ceVol/peVol against the snapshot closest to _velWin minutes ago —
    // same windowing concept the backend's oiVelocity block already applies
    // to OI Vel, just computed client-side since no equivalent volume field
    // exists in that payload.
    this._volHistory = this._volHistory || [];
    const _now = Date.now();
    const _nowSnap = {};
    chain.forEach(r=>{ _nowSnap[r.strike] = { ceVol: r.ceVol, peVol: r.peVol }; });
    this._volHistory.push({ ts: _now, snap: _nowSnap });
    const _windowMs = _velWin * 60 * 1000;
    const _cutoff = _now - _windowMs;
    // Trim history once entries are more than one window past the cutoff —
    // keeps memory bounded without discarding the reference sample we need.
    while (this._volHistory.length > 1 && this._volHistory[1].ts < _cutoff - _windowMs) this._volHistory.shift();
    // Pick the newest sample that's still at least a full window old. Until
    // enough history has accumulated (e.g. just after page load), this
    // falls back to the oldest sample available — the window is shorter
    // than _velWin for the first few minutes, then self-corrects.
    let _refSnap = this._volHistory[0].snap;
    for (const h of this._volHistory) { if (h.ts <= _cutoff) _refSnap = h.snap; else break; }
    let totCeVelVol = 0, totPeVelVol = 0;
    chain.forEach(r=>{
      const prev = _refSnap[r.strike];
      if (prev) {
        if (r.ceVol != null && prev.ceVol != null) totCeVelVol += (r.ceVol - prev.ceVol);
        if (r.peVol != null && prev.peVol != null) totPeVelVol += (r.peVol - prev.peVol);
      }
    });
    const maxVelVol=Math.max(Math.abs(totCeVelVol),Math.abs(totPeVelVol),1);
    rpEl.innerHTML=`
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:8px;">
      <div class="arp-row" style="padding-bottom:5px;margin-bottom:4px;border-bottom:1px solid var(--border);">
        <span class="arp-key">Signal</span>
        <div class="arp-val"><span class="sp ${aggBias.cls}" style="font-size:10px;font-weight:700;">${aggBias.label}</span><span style="font-size:9px;color:var(--txt3);margin-left:4px;">${bullStrikes}↑ ${bearStrikes}↓</span></div>
      </div>
      <div class="arp-row"><span class="arp-key">Net OI</span><div class="arp-val"><span class="arp-num" style="color:${signColor(netOI)};">${netOI>=0?'+':''}${fmtK(netOI)}</span><div class="arp-bar" style="width:${arpBarW(netOI,netAbsMax)}px;background:${signColor(netOI)};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Chg OI</span><div class="arp-val"><span class="arp-num" style="color:${signColor(netDOI)};">${netDOI>=0?'+':''}${fmtK(netDOI)}</span><div class="arp-bar" style="width:${arpBarW(netDOI,netAbsMax)}px;background:${signColor(netDOI)};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Vel OI</span><div class="arp-val"><span class="arp-num" style="color:${signColor(netVel)};">${netVel==null||isNaN(netVel)?'—':(netVel>=0?'+':'')+fmtK(netVel)}</span><div class="arp-bar" style="width:${arpBarW(netVel,netAbsMax)}px;background:${signColor(netVel)};"></div></div></div>
      <div style="padding-top:5px;margin-top:4px;border-top:1px solid var(--border);">
        <div style="margin-bottom:6px;">
          <div style="display:flex;justify-content:space-between;font-size:8px;font-family:var(--mono);margin-bottom:2px;">
            <span style="color:var(--red);">CE ${totCeOI>0?Math.round(totCeOI/(totCeOI+totPeOI)*100):50}%</span>
            <span style="color:var(--txt3);font-size:7px;text-transform:uppercase;letter-spacing:.05em;">OI Split</span>
            <span style="color:var(--green);">PE ${totPeOI>0?Math.round(totPeOI/(totCeOI+totPeOI)*100):50}%</span>
          </div>
          <div class="oi-flow-bar">
            <div class="oi-flow-ce" style="flex:${totCeOI>0?Math.round(totCeOI/(totCeOI+totPeOI)*100):50};"></div>
            <div class="oi-flow-pe" style="flex:${totPeOI>0?Math.round(totPeOI/(totCeOI+totPeOI)*100):50};"></div>
          </div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;">
        <span class="arp-key">PCR <span style="font-size:8px;font-weight:400;text-transform:none;">(visible)</span></span>
        <span style="font-size:14px;font-weight:700;font-family:var(--mono);color:${pcrColor};">${panelPCR}</span>
        </div>
      </div>
    </div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:8px;">
      <div class="crp-title" style="margin-bottom:6px;">OI Analytics <span style="color:var(--txt3);font-weight:400;">(${_velWin}m)</span></div>
      <div style="display:grid;grid-template-columns:64px 1fr 1fr;gap:2px;margin-bottom:4px;">
        <div></div><div class="crp-head-ce">CE</div><div class="crp-head-pe">PE</div>
      </div>
      <div class="crp-row"><span class="crp-label">OI</span><div class="crp-ce">${fmtK(totCeOI)}</div><div class="crp-pe">${fmtK(totPeOI)}</div></div>
      <div class="crp-row"><span class="crp-label">Chg OI</span>${rpBar(totCeDOI,maxDOIrp,totCeDOI>=0?'var(--red)':'var(--green)')}${rpBar(totPeDOI,maxDOIrp,totPeDOI>=0?'var(--green)':'var(--red)')}</div>
      <div class="crp-row"><span class="crp-label">OI Vel</span>${rpBar(totCeVel,maxVelrp,totCeVel>=0?'var(--red)':'var(--green)')}${rpBar(totPeVel,maxVelrp,totPeVel>=0?'var(--green)':'var(--red)')}</div>
    </div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;">
      <div class="crp-title" style="margin-bottom:6px;">Volume Analytics <span style="color:var(--txt3);font-weight:400;">(${_velWin}m)</span></div>
      <div style="display:grid;grid-template-columns:64px 1fr 1fr;gap:2px;margin-bottom:4px;">
        <div></div><div class="crp-head-ce">CE</div><div class="crp-head-pe">PE</div>
      </div>
      <div class="crp-row"><span class="crp-label">Vol</span><div class="crp-ce">${fmtK(chain.reduce((s,r)=>s+(r.ceVol||0),0))}</div><div class="crp-pe">${fmtK(chain.reduce((s,r)=>s+(r.peVol||0),0))}</div></div>
      <div class="crp-row"><span class="crp-label">Vol Chg</span>${rpBar(totCeVolChg,maxVolChg,totCeVolChg>=0?'var(--red)':'var(--green)')}${rpBar(totPeVolChg,maxVolChg,totPeVolChg>=0?'var(--green)':'var(--red)')}</div>
      <div class="crp-row"><span class="crp-label">Vol Vel</span>${rpBar(totCeVelVol,maxVelVol,totCeVelVol>=0?'var(--red)':'var(--green)')}${rpBar(totPeVelVol,maxVelVol,totPeVelVol>=0?'var(--green)':'var(--red)')}</div>
    </div>`;
  }

  // NOTE: Conviction Multiplier Gauge no longer has its own tick-refresh
  // block here — it moved inside advanced-analytics-card (see
  // buildAdvancedAnalyticsHtml), which already re-derives and
  // outerHTML-diffs its full contents, including this card, every tick.

  // 3b. Option Chain Snapshot card (main-dashboard OI/Chg OI/dOI/Volume
  // summary). This was previously only ever built once, inside the full
  // renderDashboard() rebuild (buildChainSummaryHtml(d) at the top of this
  // file) — never on a WS tick or expiry switch — so it went stale and
  // silently drifted from the OI Flow Snapshot card below (which *does*
  // refresh every tick), even though both read the exact same
  // getFilteredChain(_data) source. Same fix as oi-flow-summary-card /
  // greeks-alerts-card / atm-greeks-card just below: outerHTML-diff it in
  // here too, so all four summary cards stay in lockstep tick-to-tick.
  // FIX: this card's header (now a .nav-card-header link, previously the
  // "Full Chain" button — see components.css) was getting torn out from
  // under an in-progress click by the outerHTML swap below — same freeze
  // bug the Decision Detail guard exists for. guardKey skips the
  // destructive rebuild while a click on this card is mid-gesture;
  // dataset.lastHtml stays stale so the very next tick retries once the
  // click has committed, same as refreshDecisionBoxGuarded.
  patchOuterHtmlIfChanged('chain-summary-card', () => app.chain.buildChainSummaryHtml(_data), {
    guardKey: 'chainSummary', bindGuard: true
  });

  // 4. OI Flow Snapshot card (compact — full butterfly table now lives in
  // the OI Dashboard's Butterfly tab, see buildOiFlowSummaryHtml()).
  // buildOiFlowSummaryHtml() returns the whole card including its own
  // #oi-flow-summary-card wrapper — outerHTML (not innerHTML) so the
  // dataset-diff cache stays meaningful (it lives on the element itself,
  // which outerHTML replaces wholesale).
  patchOuterHtmlIfChanged('oi-flow-summary-card', () => buildOiFlowSummaryHtml(chain, atm, velByStrike, _data.oiVelocity));

  // 4b. Greeks Alerts card (gamma flip / short-gamma regime / theta decay)
  // — now lives in the row2 Tier-2 row alongside Chain Summary and
  // FII/DII, same outerHTML-diff treatment as the OI Flow card above, so
  // an expiry switch reflects the new expiry's Greeks immediately instead
  // of waiting for the next tick.
  patchOuterHtmlIfChanged('greeks-alerts-card', () => app.chain.buildGreeksAlertsHtml(greeks, atm, _data));

  // 4b-ii. FII/DII Sentiment card — now rendered inside the Capital Flow
  // zone (see the h += oi-flow-section block in renderDashboard above),
  // but this incremental patch still finds it fine via getElementById
  // regardless of DOM position. Previously this card only ever rebuilt on a full
  // renderDashboard() pass (it rendered near the Executive grid, outside
  // this incremental refresh function entirely) — it needs the same
  // per-tick treatment as its neighboring cards or it would visibly lag
  // behind them between full rebuilds.
  patchOuterHtmlIfChanged('fiidii-summary-card', () => buildFiiDiiSummaryCard(_data), {
    guardKey: 'fiiDiiSummary', bindGuard: true
  });

  // 4c. Institutional Activity Crux — same outerHTML-diff treatment; without
  // this it would only ever refresh on a full renderDashboard() rebuild,
  // same staleness gap the chain-summary/OI-flow/Greeks cards above it were
  // fixed for.
  // FIX: same freeze bug as the chain-summary-card guard above — this
  // card's "Strike Detail Report →" button was being destroyed mid-click
  // by the unconditional outerHTML swap on every tick. guardKey skips the
  // rebuild while a click here is in flight.
  patchOuterHtmlIfChanged('inst-activity-summary-card', () => app.exec.buildInstitutionalActivitySummaryCard(_data), {
    guardKey: 'instActivity', bindGuard: true
  });

  // NOTE: the old "6. IV Surface" refresh block (targeting the now-removed
  // #sec-iv .section-card) is gone — its content had merged into the
  // iv-hv-skew-detail-card, which is itself now removed (2026-08-01) as a
  // duplicate of Advanced Analytics' "IV Rank Details" card. Nothing
  // needs a Tier-3 refresh block anymore; ATM Greeks refreshes as part of
  // greeks-alerts-card below (4b), and Advanced Analytics refreshes as
  // part of advanced-analytics-card further down.

  // ATM Greeks — no longer a separate Tier-3 collapsible (2026-08-01);
  // folded into the Δ Greeks / Net GEX exec card, so it refreshes as part
  // of that card's own outerHTML-diff block (4b, buildGreeksAlertsHtml)
  // instead of needing its own here.

  // Volatility — new standalone Tier-3-style collapsible (IA redesign
  // step 7, first Advanced Analytics sub-card extracted out), same
  // open-state preservation as its siblings so an expanded panel
  // survives a tick refresh instead of collapsing out from under the
  // user.
  patchOuterHtmlIfChanged('volatility-card', () => app.chain.buildVolatilityHtml(_data), {
    preserveState: (el) => el.hasAttribute('open'),
    restoreState: (fresh, wasOpen) => { if(wasOpen) fresh.setAttribute('open',''); }
  });

  // Probability — second standalone Tier-3-style collapsible extracted
  // out of Advanced Analytics (IA redesign step 7, second pass — see
  // probability-view.js), same open-state preservation as Volatility
  // above.
  patchOuterHtmlIfChanged('probability-card', () => app.chain.buildProbabilityHtml(_data), {
    preserveState: (el) => el.hasAttribute('open'),
    restoreState: (fresh, wasOpen) => { if(wasOpen) fresh.setAttribute('open',''); }
  });

  // Scenario Analysis — third standalone Tier-3-style collapsible
  // extracted out of Advanced Analytics (IA redesign step 7, third pass
  // — see scenario-analysis-view.js), same open-state preservation as
  // Volatility/Probability above.
  patchOuterHtmlIfChanged('scenario-analysis-card', () => app.chain.buildScenarioAnalysisHtml(_data), {
    preserveState: (el) => el.hasAttribute('open'),
    restoreState: (fresh, wasOpen) => { if(wasOpen) fresh.setAttribute('open',''); }
  });

  // Advanced Analytics — fourth Tier-3-style collapsible, same open-state
  // preservation as its siblings above (it re-derives all remaining
  // sub-cards from live data every tick, so an open panel never goes
  // stale).
  patchOuterHtmlIfChanged('advanced-analytics-card', () => app.chain.buildAdvancedAnalyticsHtml(_data), {
    preserveState: (el) => el.hasAttribute('open'),
    restoreState: (fresh, wasOpen) => { if(wasOpen) fresh.setAttribute('open',''); }
  });

  // ── 7. Greeks & GEX panels ───────────────────────────────────────────────
  renderGreeksGex(_grkView);

  // ── 7b. IV Surface modal ─────────────────────────────────────────────────
  // See the matching BUGFIX note in renderDashboard's post-render block —
  // this was never actually called from here before, so a range-button
  // click (switchChainRange -> _rerenderChainPanels, not a full
  // renderDashboard rebuild) left the modal showing the old range's chain
  // if it happened to be open at the time.
  this.renderIvSurfaceModal();

  // ── 8. OI Velocity panel ─────────────────────────────────────────────────
  renderVelocity(_velWin);

  // ── 9. Institutional F&O Simulator + Scenario Controls ─────────────────────
  // This was missing entirely: expiry switches only ever refreshed the 8
  // panels above, so the simulator's GEX chart/stats/table/vol-grid kept
  // showing whatever expiry was loaded first, and moving the Scenario
  // Control sliders had no visible effect until the next full page reload.
  if (document.getElementById('sim-gex-canvas')) simInit();
  // Keep an open PDS-03 report current from the canonical payload tick,
  // independently of whether the simulator panel exists or is active.
  if (app.strikeDetail) app.strikeDetail.refresh();

  // ── 10. Executive dashboard (Market Health / Market Story / Top Movers) ────
  // Same gap as above — this block was only ever built once, during the
  // full renderDashboard() pass, so GEX/PCR/theta figures in these three
  // cards went stale after an expiry-only switch.
  // This wrapper sits alongside the Chain Snapshot card's nav-card-header
  // link — reuses the 'chainSummary' guard key so it doesn't get replaced
  // out from under that click either. Doesn't bind its own guard (no
  // action buttons of its own), only checks the one chain-summary-card
  // already owns.
  patchOuterHtmlIfChanged('exec-section-wrap', () => {
    // computeNetGEX (metrics.js, IA redesign step 6) — `greeks` here is
    // the same visible-range array as renderDashboard's, so this stays
    // consistent with what the Greeks/Net GEX Alerts card itself shows.
    _data.totalGEX = computeNetGEX(greeks);
    return renderExecutiveDashboard(_data);
  }, { guardKey: 'chainSummary' });

  if (window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(_data);
};
