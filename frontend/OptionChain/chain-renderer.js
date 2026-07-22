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
      if (activeExpiry && activeExpiry === pending) {
        delete sel.dataset.pendingExpiry;
      } else {
        if (sel.value !== pending) sel.value = pending;
        return;
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
    } else if (activeExpiry && sel.value !== activeExpiry) {
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
    const isBear = (d.decision?.bias==='BEARISH')||(d.compositeBias||'').toLowerCase().includes('bear');

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
    if (tickerEl) {
      const html = renderIndexTicker(d);
      if (tickerEl.outerHTML !== html) tickerEl.outerHTML = html;
    }
    const dteEl = document.getElementById('dte-display');
    if (dteEl) dteEl.textContent = (d.dte||0) + 'd';
    const timeEl = document.getElementById('time-display');
    if (timeEl) timeEl.textContent = d.refreshTime || '--';
  }

  const decEl = document.getElementById('sec-decision');
  if (decEl) decEl.outerHTML = this.renderDecisionBoxHtml(d);
};

ChainView.prototype.renderDashboard = function(d) {
  _data=d;
  const atm=activeAtm(d);
  const greeksAll=d.greeks||[];
  const straddle=(d.callPremium||0)+(d.putPremium||0);
  
  const chain=getFilteredChain(d);
  const chainStrikeSet=new Set(chain.map(r=>r.strike));
  const greeks=greeksAll.filter(g=>chainStrikeSet.has(g.strike));
  const combinedMode=true;
  
  const maxOI=Math.max(...chain.map(r=>Math.max(r.ceOI||0,r.peOI||0)),1);
  const totalGEX=greeks.reduce((s,g)=>s+(g.netGEX||0),0);
  // Market Story card (renderExecutiveDashboard) reads d.totalGEX directly —
  // it was only ever computed as a local variable here and in renderGEX(),
  // so d.totalGEX was always undefined and the card permanently showed "—".
  d.totalGEX = totalGEX;
  const isBull=(d.decision?.bias==='BULLISH')||(d.compositeBias||'').toLowerCase().includes('bull');
  const isBear=(d.decision?.bias==='BEARISH')||(d.compositeBias||'').toLowerCase().includes('bear');
  const sigs=d.signals||[];
  
  let h='';

  // ── TOP BAR (first) ──
  // Index ticker (fixed order NIFTY/BANKNIFTY/MIDCPNIFTY/SENSEX) is now
  // rendered inline inside renderTopBarHtml() itself, so no separate patch
  // call is needed here.
  h+=this.renderTopBarHtml(d, isBear);

  // ── DECISION ENGINE PANEL ──
  h+=this.renderDecisionBoxHtml(d);

  // ── LARGE EXECUTIVE BOXES (now a 3-col grid: Market Health | Market Story | Top Movers) ──
  h += renderExecutiveDashboard(d);

  // ── OPTIONS CHAIN ──
  // The dense Option Chain table itself lives as a static block outside
  // this template (see #sec-chain in the HTML) so it never gets torn down
  // by a dashboard rebuild — chain-anchor just marks where that block
  // gets moved to (right after the full-rebuild swap below), which is
  // between the Decision/Executive boxes and OI Flow. The duplicate chain
  // table + right analytics panel that used to be generated directly in
  // this template have been removed: the main dense Option Chain table
  // (see ChainDenseView.buildRowsHtml) now has the same click-a-row /
  // "▶ Greeks" toggle-all reveal, and its own #rightPanel
  // (RightPanelView.renderRightPanel) already carries the identical
  // Signal / OI Analytics / Volume Analytics boxes plus a Bid/Ask depth
  // box. velByStrike/velMax below are still needed by the OI Flow panel
  // further down this function.
  h += this.buildChainSummaryHtml(d);
  h += '<div id="chain-anchor"></div>';
  const velBlock=(d.oiVelocity||[]).find(b=>b.window===_velWin)||(d.oiVelocity||[])[0];
  const velByStrike={};
  if(velBlock&&velBlock.rows)velBlock.rows.forEach(vr=>{velByStrike[vr.strike]=vr;});
  const velMax=Math.max(...chain.map(r=>{const vr=velByStrike[r.strike]||{};return Math.max(Math.abs(vr.ceDOI||0),Math.abs(vr.peDOI||0));}),1);

  // OI BUILDUP + GREEKS/GEX (merged) — one 2-column grid. Strikes are the
  // shared x-axis across both panels, so header height, column-label row
  // height, and body-row height are pinned identical across both cards —
  // anything that isn't per-strike data (PCR toggle, biggest-build
  // summary) sits outside that stack so it can't knock rows out of line.
  h+=`<div id="sec-oi-buildup">
    ${buildOiFlowSummaryHtml(chain, atm, velByStrike)}
  </div>`;

  // ── GREEKS SUMMARY — alerts card (gamma flip / short-gamma / theta
  // decay) grouped together with the ATM Greeks numbers, since both are
  // "Greeks" info and read better side by side than split across two
  // unrelated sections. IV Surface (below) is a different data family
  // (per-strike IV skew, not Greeks) so it stays separate. #atm-greeks-card
  // gets its own id so it can be refreshed on an expiry switch the same
  // way #greeks-alerts-card already is — previously it had no id and only
  // ever updated on a full rebuild, going stale between expiry switches.
  h+=`<div id="sec-greeks-summary" class="two-col">
    ${this.buildGreeksAlertsHtml(greeks, atm, d)}
    ${this.buildAtmGreeksHtml(d)}
  </div>`;

  // ── IV SURFACE — alerts-only summary here (elevated skew / IV rank
  // extremes); the full per-strike CE/PE bar table moved into its own
  // modal (openIvSurfaceModal(), same treatment as Greeks/GEX), refreshed
  // by renderIvSurfaceModal() below rather than rebuilt inline here. ──
  h+=`<div id="sec-iv">
    ${this.buildIvAlertsHtml(d, chain, atm)}
  </div>`;
  
  // ── STRATEGIES + SIMULATOR (2-column) ──
  const strats=d.strategies||[];
  if(strats.length){
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
    const totalGEX = greeksData.reduce((s,g)=>s+(g.netGEX||0),0);
    const flipRow = findGammaFlipStrike(greeksData);
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
    // Kept separate from simRangeControls above: this slider scales the
    // CE/PE Vol/OI Ratio bars in the right-hand panel (see simRenderVolGrid
    // in panels-views.js, which multiplies each ratio by simVel) — it has
    // no effect on the GEX chart/stats that Spot Price and IV drive, so it
    // renders down next to that table instead of grouped with them here.
    const velControl = {
      id: 'vel', label: 'Vol/OI Velocity',
      min: 0.1, max: 5, step: 0.1,
      override: _simVelOverride, overrideVar: '_simVelOverride',
      base: simCtx.baseVel || 1.2, fmt: v => fmtN(v, 1) };

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

  h+=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;align-items:stretch;">

    <!-- LEFT: Strategy Payoff -->
    <div id="sec-strats" class="section-card" style="min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;">

      <div class="section-header"><span class="section-title">Strategy Payoff</span></div>

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

      <!-- Payoff chart canvas -->
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 14px 10px;position:relative;">
        <canvas id="strat-payoff-canvas" style="width:100%;display:block;" height="280"></canvas>
      </div>

      <!-- Leg pills -->
      <div id="strat-legs-row" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;align-items:center;"></div>

    
    </div>

    <!-- RIGHT: Greeks by Moneyness -->
    <div id="sec-greeks-moneyness" class="section-card" style="min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;">
      <div class="section-header"><span class="section-title">Greeks by Moneyness</span></div>
      <div style="display:flex;flex-wrap:wrap;gap:16px;margin-bottom:10px;font-size:11px;color:var(--txt3);flex-shrink:0;">
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#2a78d6;"></span>Delta (call)</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#1baf7a;"></span>Gamma</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#e34948;"></span>|Theta| decay</span>
        <span style="display:flex;align-items:center;gap:4px;"><span style="width:10px;height:5px;border-radius:2px;background:#eda100;"></span>Vega</span>
      </div>
      <!-- flex:1 + min-height:0 is the standard fix for Chart.js (responsive +
           maintainAspectRatio:false) inside a flex column: without min-height:0
           the flex item's default min-height:auto fights the canvas's own
           measurement and the box grows/shrinks abruptly on every render. -->
      <div style="position:relative;width:100%;flex:0.9;min-height:280px;">
        <canvas id="greeksChart" role="img" aria-label="Line chart showing how delta, gamma, theta, and vega change shape from deep OTM through ATM to deep ITM for a call option, updated live from the option chain.">Delta rises steadily from OTM to ITM. Gamma, theta decay, and vega all peak at the at-the-money strike and fall off toward both deep ITM and deep OTM.</canvas>
      </div>
    </div>  

  </div>

  <!-- Institutional F&O Simulator — now full-width on its own row; it
       used to share a 1fr/1fr grid with the Strike Detail expand panel,
       which cramped both the GEX chart and the Vol/OI Velocity/Strike
       Detail tables into half the available width. The detail panel now
       gets its own full-width row directly below (see #sec-simulator-detail
       further down) instead of sitting beside the simulator. -->
  <div id="sec-simulator" class="sim-wrap" style="min-width:0;margin-bottom:10px;">

      <div class="sim-header">
        <div class="sim-title">Institutional F&amp;O Simulator</div>
        <div class="sim-subtitle">Net GEX Profile &bull; Vanna Multiplier &bull; Vol/OI Velocity &bull; Dealer Regime</div>
      </div>
      <div class="sim-body" style="padding:10px 14px;">

        <!-- GEX Chart -->
        <div class="sim-chart-area" style="padding-bottom:12px;" id="sim-chart-container">
          <div class="sim-chart-label">Net GEX Profile ($B) &#8593;</div>
          <canvas id="sim-gex-canvas" height="180"></canvas>
          <div class="sim-annot" id="sim-annot"></div>
        </div>

        <!-- Dealer Regime bar — Dealer Bias dropdown sits at the right end
             of this same line (after the regime value), since it's the
             control that drives this readout. -->
        <div class="sim-regime-bar" id="sim-regime-bar">
          <span class="sim-regime-label">Dealer Regime</span>
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
            <div class="sim-stat-label">Net GEX ($B)</div>
            <div class="sim-stat-val" id="sim-stat-gex" style="color:${totalGEX>=0?'var(--blue)':'var(--red)'};">${fmtN(totalGEX,2)}</div>
            <div class="sim-stat-sub">${totalGEX>=0?'Long gamma (dampens)':'Short gamma (amplifies)'}</div>
          </div>
          <div class="sim-stat">
            <div class="sim-stat-label">Vanna Multiplier</div>
            <div class="sim-stat-val" id="sim-stat-vanna" style="color:var(--amber);">${fmtN(vannaMultiplier,2)}</div>
            <div class="sim-stat-sub">IV-flow amplifier</div>
          </div>
          <div class="sim-stat">
            <div class="sim-stat-label">Gamma Flip Strike</div>
            <div class="sim-stat-val" id="sim-stat-flip" style="color:var(--red);">${flipStrike?fmtI(flipStrike):'--'}</div>
            <div class="sim-stat-sub">Short &rarr; Long GEX</div>
          </div>
        </div>

        <!-- Simulation Controls -->
        <div style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Scenario Controls</div>
        <div class="sim-controls" style="grid-template-columns:1fr;">
          ${simRangeControls.map(renderSimRangeRow).join('')}
        </div>

      </div>

  </div>

  <!-- Strike Detail expand panel — Vol/OI Velocity + Strike Detail tables.
       Now a full-width row directly below the simulator instead of a
       cramped half-width column beside it. Still collapsed by default —
       the Institutional Activity Crux card on the main dashboard
       (buildInstitutionalActivitySummaryCard, in panels-views.js) is the
       always-visible summary of this same data; this full near/far-band
       report only opens when its "Strike Detail →" button calls
       expandStrikeDetail(). -->
  <div id="sec-simulator-detail" class="sim-wrap" style="min-width:0;margin-bottom:10px;">
    <div class="sim-body" style="padding:14px 14px;">

      <div id="sec-simulator-detail-placeholder" style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
        <div>
          <div style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px;">Vol/OI Velocity &amp; Strike Detail</div>
          <div style="font-size:11px;color:var(--txt3);">Collapsed — open via the Institutional Activity Crux card's "Strike Detail →" link above.</div>
        </div>
        <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="expandStrikeDetail()">Expand →</button>
      </div>

      <div id="sec-simulator-detail-body" style="display:none;">

        <div style="display:flex;align-items:center;justify-content:flex-end;margin-bottom:4px;">
          <button class="sec-btn" style="padding:3px 8px;font-size:10px;" onclick="collapseStrikeDetail()">Collapse ↑</button>
        </div>

        <!-- Vol/OI Velocity Breakdown -->
        <div style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Vol/OI Velocity by Strike (Block Detection)</div>
        <div class="sim-controls" style="grid-template-columns:1fr;margin-bottom:10px;">
          ${renderSimRangeRow(velControl)}
        </div>
        <div class="sim-vol-grid" id="sim-vol-grid"></div>

        <!-- Strike Table -->
        <div style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;margin-top:14px;">Strike Detail</div>
        <div class="sim-table-wrap">
          <div style="display:grid;grid-template-columns:64px 46px minmax(160px,1.4fr) 80px 50px 56px 100px minmax(140px,1fr);column-gap:6px;padding:6px 10px;border-bottom:1px solid var(--border);background:var(--bg1);">
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Strike</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Dist</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Open Interest</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">&Delta;OI Today</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;text-align:right;">IV</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;text-align:right;">Delta</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Smart Money</span>
            <span style="font-size:9px;font-weight:600;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Market Structure</span>
          </div>
          <div id="sim-strike-table"></div>
        </div>

      </div>
    </div>
  </div>
  `;
  }
  }


  const risk=d.risk||{};
  const kl=risk.keyLevels||[];
  if(risk.keyLevels||risk.ivRegime||risk.tradeGrade){
    const ivRgClr=risk.ivRegime==='Rich'?'var(--red)':risk.ivRegime==='Cheap'?'var(--green)':'var(--amber)';
    const gradeClr=risk.tradeGrade&&risk.tradeGrade.startsWith('A')?'var(--green)':risk.tradeGrade&&risk.tradeGrade.startsWith('B')?'var(--amber)':'var(--txt3)';
    h+=`<div id="sec-risk" class="section-card" style="margin-bottom:10px;min-width:0;">
      <div class="section-header"><span class="section-title">Risk dashboard</span></div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;margin-bottom:10px;">
        ${risk.tradeGrade&&risk.tradeGrade!=='—'?`<div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;"><div style="font-size:10px;color:var(--txt3);margin-bottom:3px;">Trade grade</div><div style="font-size:18px;font-weight:800;color:${gradeClr};">${risk.tradeGrade}</div></div>`:''}
        ${risk.ivRegime?`<div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;"><div style="font-size:10px;color:var(--txt3);margin-bottom:3px;">IV regime</div><div style="font-size:13px;font-weight:700;color:${ivRgClr};">${risk.ivRegime}</div><div style="font-size:10px;color:var(--txt3);">IV−HV ${risk.ivHvSpread>=0?'+':''}${fmtN(risk.ivHvSpread,2)}%</div></div>`:''}
        ${risk.trapWarn&&risk.trapWarn.toLowerCase()!=='none'?`<div style="background:rgba(250,82,82,0.08);border:1px solid rgba(250,82,82,0.3);border-radius:6px;padding:8px 10px;"><div style="font-size:10px;color:var(--txt3);margin-bottom:3px;">Trap warning</div><div style="font-size:12px;font-weight:700;color:var(--red);">${risk.trapWarn}</div></div>`:''}
      </div>
      ${kl.length?`<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;">
      ${kl.map(k=>{const c=k.cls==='bull'?'var(--green)':k.cls==='bear'?'var(--red)':'var(--txt2)';return `<div style="text-align:center;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:6px 8px;"><div style="font-size:9px;color:var(--txt3);text-transform:uppercase;margin-bottom:2px;">${k.label}</div><div style="font-size:12px;font-weight:700;font-family:var(--mono);color:${c};">${fmtI(k.value)}</div></div>`;}).join('')}
      </div>`:''}
    </div>`;
  }



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
  const _oldSimDetailSection = _keepInteractiveSubtrees ? document.getElementById('sec-simulator-detail') : null;

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
  $i('dashboard').innerHTML = h;
  if(dashEl) dashEl.dataset.stratsSig = _stratsSig;
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
    const fresh = document.getElementById('sec-simulator-detail');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldSimDetailSection, fresh);
  }

  // Drop the dense Option Chain block into the anchor point between
  // Decision/Executive boxes and OI Flow. Runs on every full rebuild
  // (not gated by _keepInteractiveSubtrees) since the chain block isn't
  // regenerated by this template at all — only relocated.
  // _chainRightPanel already lives INSIDE _chainSection — it's the second
  // grid column of .chain-layout (see #sec-chain / #rightPanel markup in
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
  setTimeout(function(){simInit();},50);
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
  if(!_data || !selectedExpiry) return;
  const activeExpiry = _data.expiry || '';
  if(selectedExpiry === activeExpiry) return; // already showing this expiry, nothing to do

  // ── PIN THE DROPDOWN TO THE USER'S PICK ──
  // The <select> already shows selectedExpiry the instant the browser fires
  // this onchange — that's native behavior, free. The problem is everything
  // AFTER this point: _data still holds the OLD expiry until the new
  // connection's first "full" payload lands, and anything that re-renders
  // the dropdown off stale _data.expiry in that window (a race with an
  // in-flight tick from the old connection, or a delta payload that never
  // carries an "expiry" field) stomps the user's visible selection back to
  // the old value even though the data underneath is already moving to the
  // new expiry. Tagging the node with a pending marker + value lets
  // renderExpiryOptions (below) recognize "this is the expiry we're
  // mid-switch to" and keep deferring to it instead of payload.expiry until
  // the real payload actually confirms the switch.
  const sel = (typeof getExpirySelectNode === 'function') ? getExpirySelectNode() : null;
  if (sel) {
    sel.value = selectedExpiry;
    sel.dataset.pendingExpiry = selectedExpiry;
  }

  // Single-expiry-per-connection model: the server only ever fetches/builds
  // the ONE expiry's chain a client is connected with (NO_EXTRA_CHAINS
  // defaults on in ws_server_live.py now), so there's no second expiry's
  // chain/chainMeta sitting in the payload to splice in locally any more.
  // Reconnecting with ?expiry=... re-points the whole backend pipeline at
  // the new expiry (ws_handler -> switch_symbol -> _resolve_chain_tokens);
  // the resulting "full" snapshot on the new connection is what actually
  // repaints every chain-derived panel, same as any other WS full/delta
  // handling in updateDashboard's tick handler.
  const base = (_wsUrl || '').split('?')[0];
  const params = new URLSearchParams((_wsUrl || '').split('?')[1] || '');
  params.set('expiry', selectedExpiry);
  connectWebSocket(`${base}?${params.toString()}`);
};

ChainView.prototype._rerenderChainPanels = function() {
  if(!_data) return;

  const chain          = getFilteredChain(_data);
  const chainStrikeSet = new Set(chain.map(r=>r.strike));
  const atm            = activeAtm(_data);
  const greeksAll      = _data.greeks || [];
  const greeks         = greeksAll.filter(g=>chainStrikeSet.has(g.strike));
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
      rows+=`<tr${ia?' id="chain-row-atm"':''} style="cursor:pointer;" onclick="toggleGreekRow(${sk})" title="${rowTitle}">`;
      rows+=`<td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.ceVol)}</td>
        <td class="${ac}">${velMiniCell(ceVelDOI,velMax,ceOiChgClr(ceVelDOI))}</td>
        <td class="${ac} pt-ltp-click" style="font-weight:600;font-family:var(--mono);" onclick="ptOpenQuickOrder(event,${sk},'CE',${r.ceLTP!=null?r.ceLTP:'null'})" title="Click to trade this strike">${fmtN(r.ceLTP,1)}</td>
        <td class="${ac}" style="color:${ceOiChgClr(r.ceDOI)};font-size:10px;">${(r.ceDOI||0)>=0?'+':''}${fmtK(r.ceDOI)}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.ceOI)}</td>
        <td class="${acs}" style="white-space:nowrap;line-height:1.15;">${fmtI(r.strike)}${ia?' ★':''}</td>
        <td class="${ac}" style="font-size:10px;color:var(--txt2);">${fmtK(r.peOI)}</td>
        <td class="${ac}" style="color:${sClr(r.peDOI)};font-size:10px;">${(r.peDOI||0)>=0?'+':''}${fmtK(r.peDOI)}</td>
        <td class="${ac} pt-ltp-click" style="font-weight:600;font-family:var(--mono);" onclick="ptOpenQuickOrder(event,${sk},'PE',${r.peLTP!=null?r.peLTP:'null'})" title="Click to trade this strike">${fmtN(r.peLTP,1)}</td>
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
    const arpClr=(v)=>v>=0?'var(--green)':'var(--red)';
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
      <div class="arp-row"><span class="arp-key">Net OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netOI)};">${netOI>=0?'+':''}${fmtK(netOI)}</span><div class="arp-bar" style="width:${arpBarW(netOI,netAbsMax)}px;background:${arpClr(netOI)};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Chg OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netDOI)};">${netDOI>=0?'+':''}${fmtK(netDOI)}</span><div class="arp-bar" style="width:${arpBarW(netDOI,netAbsMax)}px;background:${arpClr(netDOI)};"></div></div></div>
      <div class="arp-row"><span class="arp-key">Vel OI</span><div class="arp-val"><span class="arp-num" style="color:${arpClr(netVel)};">${netVel==null||isNaN(netVel)?'—':(netVel>=0?'+':'')+fmtK(netVel)}</span><div class="arp-bar" style="width:${arpBarW(netVel,netAbsMax)}px;background:${arpClr(netVel)};"></div></div></div>
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

  // 3b. Option Chain Snapshot card (main-dashboard OI/Chg OI/dOI/Volume
  // summary). This was previously only ever built once, inside the full
  // renderDashboard() rebuild (buildChainSummaryHtml(d) at the top of this
  // file) — never on a WS tick or expiry switch — so it went stale and
  // silently drifted from the OI Flow Snapshot card below (which *does*
  // refresh every tick), even though both read the exact same
  // getFilteredChain(_data) source. Same fix as oi-flow-summary-card /
  // greeks-alerts-card / atm-greeks-card just below: outerHTML-diff it in
  // here too, so all four summary cards stay in lockstep tick-to-tick.
  const chainSummaryEl = document.getElementById('chain-summary-card');
  if (chainSummaryEl) {
    const freshChainSummary = app.chain.buildChainSummaryHtml(_data);
    if (chainSummaryEl.dataset.lastHtml !== freshChainSummary) {
      chainSummaryEl.outerHTML = freshChainSummary;
      const fresh = document.getElementById('chain-summary-card');
      if (fresh) fresh.dataset.lastHtml = freshChainSummary;
    }
  }

  // 4. OI Flow Snapshot card (compact — full butterfly table now lives in
  // the OI Dashboard's Butterfly tab, see buildOiFlowSummaryHtml()).
  const oiFlowSummaryEl = document.getElementById("oi-flow-summary-card");
  if(oiFlowSummaryEl){
    const freshOiFlowSummary = buildOiFlowSummaryHtml(chain, atm, velByStrike);
    // buildOiFlowSummaryHtml() returns the whole card including its own
    // #oi-flow-summary-card wrapper — swap the wrapper's contents/attrs via
    // outerHTML so setHtmlIfChanged's dataset-diff cache stays meaningful
    // (it lives on the element itself, which outerHTML replaces wholesale).
    if(oiFlowSummaryEl.dataset.lastHtml !== freshOiFlowSummary){
      oiFlowSummaryEl.outerHTML = freshOiFlowSummary;
      const fresh = document.getElementById("oi-flow-summary-card");
      if(fresh) fresh.dataset.lastHtml = freshOiFlowSummary;
    }
  }

  // 4b. Greeks summary — alerts card (gamma flip / short-gamma regime /
  // theta decay) and the ATM Greeks card next to it, same outerHTML-diff
  // treatment as the OI Flow card above, so an expiry switch reflects the
  // new expiry's Greeks immediately instead of waiting for the next tick
  // (or, for ATM Greeks, the next full rebuild — it had no id before and
  // never got an incremental refresh at all).
  const greeksAlertsEl = document.getElementById("greeks-alerts-card");
  if(greeksAlertsEl){
    const freshGreeksAlerts = app.chain.buildGreeksAlertsHtml(greeks, atm, _data);
    if(greeksAlertsEl.dataset.lastHtml !== freshGreeksAlerts){
      greeksAlertsEl.outerHTML = freshGreeksAlerts;
      const fresh = document.getElementById("greeks-alerts-card");
      if(fresh) fresh.dataset.lastHtml = freshGreeksAlerts;
    }
  }
  const atmGreeksEl = document.getElementById("atm-greeks-card");
  if(atmGreeksEl){
    const freshAtmGreeks = app.chain.buildAtmGreeksHtml(_data);
    if(atmGreeksEl.dataset.lastHtml !== freshAtmGreeks){
      atmGreeksEl.outerHTML = freshAtmGreeks;
      const fresh = document.getElementById("atm-greeks-card");
      if(fresh) fresh.dataset.lastHtml = freshAtmGreeks;
    }
  }

  // 4c. Institutional Activity Crux — same outerHTML-diff treatment; without
  // this it would only ever refresh on a full renderDashboard() rebuild,
  // same staleness gap the chain-summary/OI-flow/Greeks cards above it were
  // fixed for.
  const instActivityEl = document.getElementById("inst-activity-summary-card");
  if(instActivityEl){
    const freshInstActivity = app.exec.buildInstitutionalActivitySummaryCard(_data);
    if(instActivityEl.dataset.lastHtml !== freshInstActivity){
      instActivityEl.outerHTML = freshInstActivity;
      const fresh = document.getElementById("inst-activity-summary-card");
      if(fresh) fresh.dataset.lastHtml = freshInstActivity;
    }
  }

  // ── 6. IV Surface ─────────────────────────────────────────────────────────
  // Refresh the compact alerts card (same builder used on initial render),
  // not the full per-strike table — that lives only in the modal, refreshed
  // separately by renderIvSurfaceModal(). This used to rebuild the full
  // table inline here, which duplicated the modal's content on the main
  // dashboard and wasted space; fixed to match how Greeks refreshes above.
  const ivSurfEl = document.querySelector('#sec-iv .section-card');
  if(ivSurfEl){
    const freshIvAlerts = app.chain.buildIvAlertsHtml(_data, chain, atm);
    if(ivSurfEl.dataset.lastHtml !== freshIvAlerts){
      ivSurfEl.outerHTML = freshIvAlerts;
      const fresh = document.querySelector('#sec-iv .section-card');
      if(fresh) fresh.dataset.lastHtml = freshIvAlerts;
    }
  }

  // ── 7. Greeks & GEX panels ───────────────────────────────────────────────
  renderGreeksGex(_grkView);

  // ── 8. OI Velocity panel ─────────────────────────────────────────────────
  renderVelocity(_velWin);

  // ── 9. Institutional F&O Simulator + Scenario Controls ─────────────────────
  // This was missing entirely: expiry switches only ever refreshed the 8
  // panels above, so the simulator's GEX chart/stats/table/vol-grid kept
  // showing whatever expiry was loaded first, and moving the Scenario
  // Control sliders had no visible effect until the next full page reload.
  if (document.getElementById('sim-gex-canvas')) simInit();

  // ── 10. Executive dashboard (Market Health / Market Story / Top Movers) ────
  // Same gap as above — this block was only ever built once, during the
  // full renderDashboard() pass, so GEX/PCR/theta figures in these three
  // cards went stale after an expiry-only switch.
  const execWrap = document.getElementById('exec-section-wrap');
  if (execWrap) {
    _data.totalGEX = greeks.reduce((s,g)=>s+(g.netGEX||0),0);
    execWrap.outerHTML = renderExecutiveDashboard(_data);
  }

  if (window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(_data);
};
