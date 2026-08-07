// ============================================================
// chain-template.js
// Phase 2 chain-view decomposition — see chain-view.js's header comment
// for the full split rationale and load-order requirement (this file
// must load after chain-view.js, and before dashboard.js).
//
// This file holds ChainView's pure HTML-template-building methods: given
// already-computed data, they return an HTML string and touch no DOM
// themselves (the callers in chain-renderer.js are the ones that write
// the returned string into the page). Moved verbatim from chain-views.js
// — see that file's git history / the master optimization prompt for the
// original combined source.
// ============================================================

  // Builds the <option> list for the top-bar symbol picker.
  //
  // d.fnoSymbols — { indices: [...], stocks: [...] } — is sent by the
  // backend (mTerminals_json.py -> smartapi_client.get_fno_underlyings())
  // and covers EVERY NSE/BSE underlying that currently has live F&O
  // contracts, not just the old 6-symbol COMMON_SYMBOLS shortlist. It's
  // only sent on a full snapshot (not every delta tick), so it's cached
  // on the instance the first time it's seen and reused after that.
  //
  // If the currently active symbol isn't in the cached list for some
  // reason (backend hasn't sent fnoSymbols yet, or --symbol was started
  // with something the ScripMaster doesn't recognize), it's prepended to
  // "Indices" so the dropdown always shows the true current value instead
  // of silently falling back to the first option.
ChainView.prototype.renderSymbolOptions = function(active, fnoSymbols) {
    if (fnoSymbols && (fnoSymbols.indices || fnoSymbols.stocks)) {
      this._fnoSymbolsCache = fnoSymbols;
    }
    const universe = this._fnoSymbolsCache;

    if (!universe) {
      // Fallback while waiting on the first full snapshot: the old
      // hardcoded shortlist plus a manual "Other…" entry.
      const list = COMMON_SYMBOLS.includes(active) ? COMMON_SYMBOLS : [active, ...COMMON_SYMBOLS];
      return list.map(s=>`<option value="${s}"${s===active?' selected':''}>${s}</option>`).join('')
        + `<option value="__other__">Other…</option>`;
    }

    let indices = universe.indices || [];
    const stocks = universe.stocks || [];
    if (!indices.includes(active) && !stocks.includes(active)) indices = [active, ...indices];

    const opt = s => `<option value="${s}"${s===active?' selected':''}>${s}</option>`;
    return `<optgroup label="Indices">${indices.map(opt).join('')}</optgroup>`
      + `<optgroup label="Stocks">${stocks.map(opt).join('')}</optgroup>`;
};

ChainView.prototype.renderTopBarHtml = function(d, isBear) {
  const feedState = (window.AppState && AppState.feedState) || {status:'CONNECTING'};
  const feedStatus = (feedState.status || 'CONNECTING').toLowerCase();
  const feedLabel = feedState.status || 'CONNECTING';
  if (isBear === undefined) {
    isBear = isBearBias(d);
  }
  // Flash direction vs the last tick actually rendered — see the
  // .tick-flash-up/-down keyframes in styles.css for why `animation`
  // (not `transition`) is what makes this visible despite the top-bar
  // being a brand-new DOM node every tick (outerHTML rebuild below).
  // Reset the baseline on a symbol switch first — NIFTY (~24,000) vs
  // BANKNIFTY (~51,000) are different scales entirely, comparing across
  // that boundary would flash a huge, meaningless "move" on the first
  // tick of the new symbol.
  if (d.symbol && d.symbol !== this._lastSpotSymbol) {
    this._lastSpot = null;
    this._lastSpotSymbol = d.symbol;
  }
  const spotNum = Number(d.spot);
  let spotFlashCls = '';
  if (this._lastSpot !== null && !isNaN(spotNum) && spotNum !== this._lastSpot) {
    spotFlashCls = spotNum > this._lastSpot ? ' tick-flash-up' : ' tick-flash-down';
  }
  if (!isNaN(spotNum)) this._lastSpot = spotNum;
  return `<div id="sec-topbar" class="top-bar">
    <div class="top-bar-left">
      <!-- Symbol is now a picker, not static text — picking a value calls
           the same switchActiveIndex(sym) the index-ticker pills already
           use (reconnects WS with ?symbol=..., see ws_handler's
           switch_symbol() on the backend), so this single running
           DashboardPro.html instance switches to whatever symbol you pick
           instead of needing a second backend/window per symbol. The
           persistent-node re-parenting trick isn't needed here (unlike
           #expirySelect) since this rebuilds fresh each render anyway and
           doesn't need to preserve mid-edit state between ticks.
           renderSymbolOptions() below fills in the full backend-supplied
           F&O universe (d.fnoSymbols — every NSE/BSE underlying with live
           F&O contracts, grouped Indices/Stocks) plus whatever custom
           symbol is currently active if it isn't already in that list. -->
      <select id="symbolSelect" class="symbol symbol-select" title="Switch active symbol" onchange="onSymbolPicked(this.value)">${this.renderSymbolOptions(d.symbol||'NIFTY', d.fnoSymbols)}</select>
      <!-- Manual EQ/FUT price-source selector — see setPriceSource() in
           chain-helpers.js. d.priceSource comes straight off the backend
           payload (mTerminals_json.py's export_dashboard_json()); default
           "EQ" if the field hasn't arrived yet on a very first render. -->
      <select id="priceSourceSelect" class="price-source-select" title="Spot price source (EQ goes stale ~3:15-3:30; switch to FUT to keep evaluating)" onchange="onPriceSourcePicked(this.value)">
        <option value="EQ"${(d.priceSource||'EQ')==='EQ'?' selected':''}>EQ</option>
        <option value="FUT"${d.priceSource==='FUT'?' selected':''}>FUT</option>
      </select>
      <!-- Only matters once priceSource=FUT is picked above, but shown
           always for a stable layout rather than popping in/out. -->
      <select id="futuresExpirySelect" class="price-source-select futures-expiry-select" title="Which monthly futures contract to use as FUT source" onchange="onFuturesExpiryPicked(this.value)">
        <option value="NEAR"${(d.futuresExpiry||'NEAR')==='NEAR'?' selected':''}>NEAR</option>
        <option value="NEXT"${d.futuresExpiry==='NEXT'?' selected':''}>NEXT</option>
        <option value="FAR"${d.futuresExpiry==='FAR'?' selected':''}>FAR</option>
      </select>
      <span id="topbar-spot" class="spot${isBear?' bearish':''}${spotFlashCls}">${fmtI(d.spot)}${d.priceSource==='FUT'?` <small class="price-source-tag">(FUT ${d.futuresExpiry||'NEAR'})</small>`:''}</span>
      ${d.spotChgPct!==undefined?`<span id="topbar-badge" class="badge ${d.spotChgPct>=0?'badge-bull':'badge-bear'}">${d.spotChgPct>=0?'▲':'▼'} ${Math.abs(d.spotChgPct).toFixed(2)}% (${d.spotChange>=0?'+':''}${Math.round(d.spotChange||0)})</span>`:''}
      ${renderIndexTicker(d)}
    </div>
    <div class="expiry-strip">
      <div class="expiry-pill feed-health-pill">
        <span class="expiry-pill-label">Feed</span>
        <span class="feed-status-pill" id="feed-status-pill" data-status="${feedStatus}" title="${feedState.reason || ''}">${feedLabel}</span>
      </div>
      <div class="expiry-divider"></div>
      <!-- Expiry is its own dedicated pill, separate from DTE, and sits
           leftmost in the strip. The same persistent <select> node from
           #expiry-select-holder is re-parented into #expiry-slot on every
           render (see moveExpirySelectIntoTopBar()) rather than rebuilt,
           so its option list and current value survive live ticks. -->
      <div class="expiry-pill">
        <span class="expiry-pill-label">Expiry</span>
        <span id="expiry-slot"></span>
      </div>
      <div class="expiry-divider"></div>
      <div class="expiry-pill">
        <span class="expiry-pill-label">DTE</span>
        <span class="expiry-pill-val dte-val" id="dte-display">${(d.dte||0)}d</span>
      </div>
      <div class="expiry-divider"></div>
      <div class="expiry-pill">
        <span class="expiry-pill-label">As of</span>
        <span class="expiry-pill-val time-val" id="time-display">${d.refreshTime||'--'}</span>
      </div>
    </div>
  </div>`;
};

  // The P&L/Fund pills that used to render here (renderFundPillHtml,
  // clickable, opening the old combined #pt-panel) have been removed
  // (2026-08-04) — that readout now lives permanently in the Portfolio
  // Tracker panel (#pt-portfolio-panel, opened from the "Portfolio"
  // #pt-toggle-btn in the left #sec-nav-bar rail) instead of duplicating
  // it in the top-bar's already-crowded expiry strip. ptComputeFundSummary()
  // in paper-trading.js is still the source of that data — it's just
  // consumed directly by ptRenderPortfolioSummary() now, not here.

  // ── MINI SPARKLINE — small live price trace shown in the Decision
  // Engine card's header row, between the bias call and Confidence.
  // Clicking it opens the same standalone chart as the top-bar chart
  // icon (renderTopBarHtml above) — same URL, same new-tab behavior,
  // just a second, more glanceable entry point that also gives a
  // preview of the actual price action instead of a bare icon.
  //
  // History buffer lives on `this` (the ChainView singleton, app.chain)
  // rather than as a module-level var — same pattern as this._lastSpot
  // above — so it survives the #sec-decision outerHTML swap that
  // happens on every live tick (patchTopBarAndDecision in
  // chain-renderer.js) instead of resetting to a single point each time.
  // Capped at MINI_CHART_MAX_POINTS; a symbol switch clears it the same
  // way renderTopBarHtml resets _lastSpot, so BANKNIFTY ticks never
  // trail in after a switch away from NIFTY mid-buffer.
ChainView.prototype._buildMiniChartHtml = function(d) {
    const MINI_CHART_MAX_POINTS = 150;
    if (!this._miniChartHistory || (d.symbol && d.symbol !== this._miniChartSymbol)) {
      this._miniChartHistory = [];
      this._miniChartSymbol = d.symbol;
      // New symbol means the old hydration (if any) belongs to a
      // different history entirely — clear the guard so the fetch below
      // runs again for the newly-active symbol instead of staying
      // permanently skipped because *some* symbol was hydrated once.
      this._miniChartHydratedSymbol = null;
    }
    const spotNum = Number(d.spot);
    if (!isNaN(spotNum)) {
      const hist = this._miniChartHistory;
      const last = hist[hist.length - 1];
      if (!last || last.p !== spotNum) {
        hist.push({ p: spotNum });
        if (hist.length > MINI_CHART_MAX_POINTS) hist.shift();
      }
    }

    // ── BACKFILL FROM REAL HISTORY ──
    // This buffer is pure in-memory tick accumulation — it starts empty on
    // every page load/refresh and only grows as live spot ticks arrive. On
    // a non-trading session (after close, weekend, holiday) no tick ever
    // changes, so a fresh load shows nothing but the flat dashed
    // placeholder forever, even though the backend's own /api/history
    // (the same endpoint price-chart.js's history-loader.js already uses)
    // has the last real session's candles sitting right there. Same
    // principle as that chart's render(): show the last real session's
    // shape frozen rather than an empty/placeholder trace. Guarded by
    // _miniChartHydratedSymbol/_miniChartHydrating so this fires once per
    // symbol, not on every render call.
    if (this._miniChartHistory.length < 2 && !this._miniChartHydrating && this._miniChartHydratedSymbol !== d.symbol) {
      this._miniChartHydrating = true;
      const symForFetch = d.symbol || 'NIFTY';
      fetch(`${Config.api.history}?symbol=${encodeURIComponent(symForFetch)}&range=5m`)
        .then(res => res.ok ? res.json() : [])
        .then(rows => {
          // Bail if the symbol moved on again while this was in flight, or
          // a live tick already beat the fetch back and started filling
          // the real buffer — don't clobber newer data with a stale fetch.
          if (this._miniChartSymbol !== symForFetch || this._miniChartHistory.length >= 2) return;
          if (Array.isArray(rows) && rows.length) {
            const bars = rows
              .map(r => ({ p: parseFloat(r.c) }))
              .filter(r => Number.isFinite(r.p));
            if (bars.length) this._miniChartHistory = bars.slice(-MINI_CHART_MAX_POINTS);
          }
        })
        .catch(() => { /* leave the placeholder up — nothing else to show */ })
        .finally(() => {
          this._miniChartHydrating = false;
          this._miniChartHydratedSymbol = symForFetch;
          // Swap the placeholder for the real trace in place, without
          // waiting on the next live tick — on a closed market there may
          // never be one this session.
          if (typeof this.patchTopBarAndDecision === 'function') {
            const payload = (typeof _data !== 'undefined' && _data) ? _data : d;
            this.patchTopBarAndDecision(payload);
          }
        });
    }

    const pts = this._miniChartHistory;
    const W = 280, H = 90, PAD = 6;
    let svgInner;
    if (pts.length < 2) {
      // Not enough ticks yet for a meaningful trace — flat placeholder
      // line rather than an empty box, so the widget's footprint/click
      // target is identical from the very first render.
      svgInner = `<line x1="${PAD}" y1="${H/2}" x2="${W-PAD}" y2="${H/2}" stroke="var(--text-tertiary)" stroke-width="2.5" stroke-dasharray="4,4"/>`;
    } else {
      const vals = pts.map(p => p.p);
      const min = Math.min(...vals), max = Math.max(...vals);
      const mid = (max + min) / 2;
      const dataSpan = max - min;
      // Floor the visible span at 0.15% of the price level so a genuine
      // 1-2 point wiggle on a ~24,000 index doesn't get stretched to fill
      // the entire chart height — it only "zooms in" once the real move
      // exceeds this noise floor. Centered on the data's own midpoint so
      // small ranges don't get pinned to one edge.
      const MIN_SPAN_PCT = 0.0015;
      const span = Math.max(dataSpan, mid * MIN_SPAN_PCT) || 1;
      const yMin = mid - span / 2;
      const stepX = (W - PAD * 2) / (pts.length - 1);
      const up = vals[vals.length - 1] >= vals[0];
      const color = up ? 'var(--pos)' : 'var(--neg)';
      const coords = vals.map((v, i) => {
        const x = PAD + i * stepX;
        const y = PAD + (H - PAD * 2) * (1 - (v - yMin) / span);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      svgInner = `<polyline points="${coords}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
    }

    return `
      <div id="verdict-mini-chart" class="verdict-mini-chart" role="button" tabindex="0" aria-label="Open price chart in a new tab" title="Open price chart"
           onclick="window.open('../PriceChart/price-chart.html?symbol=${encodeURIComponent(d.symbol||'NIFTY')}','_blank')"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.open('../PriceChart/price-chart.html?symbol=${encodeURIComponent(d.symbol||'NIFTY')}','_blank');}">
        <svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${svgInner}</svg>
      </div>`;
};

ChainView.prototype.renderDecisionBoxHtml = function(d, opts) {
    const detailOpen = !!(opts && opts.open);
    const dec  = d.decision || {};
    const vrd  = dec.verdicts || {};
    const sigs = dec.activeSignals || [];
    const bias = dec.bias || d.compositeBias || '—';
    const str  = dec.biasStrength || '';
    const conf = dec.confidence || 0;
    const act  = dec.action || '—';
    const conflict = dec.conflictFlag || false;

    const biasIsBull = bias === 'BULLISH';
    const biasIsBear = bias === 'BEARISH';
    const biasCardCls = biasIsBull ? 'bullish' : biasIsBear ? 'bearish' : 'neutral';
    const confColor  = conf >= 65 ? 'var(--pos)' : conf >= 40 ? 'var(--warn)' : 'var(--neg)';

    const sevDot = s => s === 'warn' ? '\u26A0' : s === 'ok' ? '\u2713' : '\u00B7';
    const sevClr = s => s === 'warn' ? 'var(--neg)' : s === 'ok' ? 'var(--pos)' : 'var(--text-tertiary)';

    // ATM IV — the one number in decision.verdicts that isn't already
    // shown elsewhere on this card (PCR/VIX/Max Pain are in the Tier-1
    // strip above; CE Wall/PE Wall are just R1/S1 restated in the S&R
    // Levels grid below). Everything else that verdictDefs used to list
    // was duplicate copy, so this card no longer carries a separate
    // "Verdicts" section at all — just this one fact, folded into S&R
    // Levels as a fifth cell.
    const atmIvTxt = vrd.atmIV ? vrd.atmIV + (vrd.ivRank ? ' · ' + vrd.ivRank.split('—')[0].trim() : '') : null;

    // ── Risk row (Trade Grade / IV Regime / CE Wall / PE Wall / Trap
    // Warning) — previously its own standalone "🛡️ Risk Dashboard"
    // section-card lower on the page (#sec-risk); folded up into the
    // Tier-1 verdict card as a second always-visible row so these get
    // seen without scrolling, instead of being one more competing card.
    // CE Wall/PE Wall pulled straight from d.ceWall/d.peWall — same
    // source as the R1/S1 cells in the S&R Levels grid below (they're
    // the same two numbers, just under their trading-desk names here vs.
    // support/resistance names there).
    const risk = d.risk || {};
    const ivRgColor = risk.ivRegime === 'Rich' ? 'var(--neg)' : risk.ivRegime === 'Cheap' ? 'var(--pos)' : 'var(--warn)';
    const gradeColor = risk.tradeGrade && risk.tradeGrade.startsWith('A') ? 'var(--pos)' : risk.tradeGrade && risk.tradeGrade.startsWith('B') ? 'var(--warn)' : 'var(--text-tertiary)';
    const hasTrapWarn = risk.trapWarn && risk.trapWarn.toLowerCase() !== 'none';

    // PCR/VIX verdict fields carry a full narrative sentence (e.g. "0.85
    // — Balanced OI · no clear directional edge"), not a short number —
    // fine for the old wider layout, but it force-wraps to 2+ lines in
    // this compact 4-column strip and throws off the row's height/
    // alignment against the neighboring 2-line boxes. Show just the lead
    // figure/segment here; the full sentence is still reachable via the
    // title tooltip on hover.
    const shortVal = (s) => {
      if (!s) return '—';
      const idx = s.indexOf('—');
      return (idx > -1 ? s.slice(0, idx) : s).trim() || '—';
    };
    // Companion to shortVal — the narrative segment that shortVal trims
    // off, restored as its own clamped single line under the value/pair
    // lines rather than dropped. Clamped (not left to wrap freely) so a
    // long sentence can't blow the row height out again the way the
    // untruncated value did; full text still reachable via title tooltip.
    const explainVal = (s) => {
      if (!s) return '';
      const idx = s.indexOf('—');
      return idx > -1 ? s.slice(idx + 1).trim() : '';
    };

    const atm = (typeof activeAtm === 'function') ? activeAtm(d) : (d.atm || 0);

    // CE Wall / PE Wall now rendered in the same 🏛️-tile style OI Flow
    // Snapshot used (build-rows with strike + OI delta), not the old flat
    // single-figure verdict-stat-line — same underlying computation
    // (OiFlowView.findOiBiggestBuild, mode 'oi', on the same filtered
    // chain _rerenderChainPanels/getFilteredChain already uses elsewhere),
    // just relabeled CE Wall/PE Wall here instead of Biggest CE OI/Biggest
    // PE OI. Both the tile's "Biggest Build" header text and its 🏛️ icon
    // were dropped — CE Wall/PE Wall rows read fine on their own. The OI
    // Flow Snapshot card's own copy of this tile was removed as a
    // duplicate once this one shipped — see buildOiFlowSummaryHtml in
    // oi-flow-view.js.
    const wallChain = (typeof getFilteredChain === 'function') ? getFilteredChain(d) : (d.chain || []);
    const wallBuild = (typeof app !== 'undefined' && app.oiFlow && typeof app.oiFlow.findOiBiggestBuild === 'function')
      ? app.oiFlow.findOiBiggestBuild(wallChain, {}, 'oi')
      : { ceStrike: null, ceVal: 0, peStrike: null, peVal: 0 };

    return `
<!-- Single root wrapper is required here: chain-renderer.js's live-tick
     path does document.getElementById('sec-decision').outerHTML =
     renderDecisionBoxHtml(d) — a one-node-for-one-node swap. Returning two
     sibling top-level elements (the .verdict card + the Decision Detail
     <details>) broke that contract — only the element carrying the
     #sec-decision id got replaced each tick, while the <details> sibling
     inserted next to it was never targeted for removal, so a new one
     piled up on top of the last on every single tick. Wrapping both in
     one #sec-decision container restores the 1-for-1 swap. -->
<div id="sec-decision">

  <!-- ── TIER 1 — the decision, at a glance. Header row: label + big bias
       call (left), Confidence + action message stacked as two lines
       (center-right), Trade Grade alone (far right). Strip below: PCR /
       India VIX (with IV Regime as an inline badge) / Max Pain + ATM
       Strike stacked / CE Wall + PE Wall stacked — Spot dropped from this
       strip since the top bar's big spot readout already covers it (see
       renderTopBarHtml/renderIndexTicker in dashboard.js). Trade Grade/IV
       Regime/CE Wall/PE Wall are folded up from the standalone Risk
       Dashboard section, which has been removed (see chain-renderer.js);
       Trap Warning from that same section moved to the Decision Detail
       collapsible below instead, since it's supporting detail rather than
       an at-a-glance number. Everything else that used to live in this
       card (Active Signals, the Verdicts breakdown, S&R Levels, Strategy
       name) is likewise supporting detail, so it moved to Decision Detail
       — same Tier-3 treatment as ATM Greeks / IV vs HV further down the
       page. ── -->
  <div class="verdict ${biasCardCls}">
    <div class="verdict-top">
      <div>
        <div class="verdict-label">Decision Engine</div>
        <div class="verdict-call">${bias}${str?' · '+str:''}${conflict?' ⚡':''}</div>
        ${d.futSignal && d.futSignal !== bias ? `<div class="verdict-fut">Fut: <strong style="color:${biasCls(d.futSignal).includes('bull')?'var(--pos)':biasCls(d.futSignal).includes('bear')?'var(--neg)':'var(--warn)'}">${d.futSignal}</strong></div>` : ''}
      </div>
      ${this._buildMiniChartHtml(d)}
      <div class="verdict-conf">
        <div class="verdict-conf-label">Confidence</div>
        <div class="verdict-conf-big" style="color:${confColor};">${conf}%</div>
        ${act && act !== '—' ? `<div class="verdict-conf-msg">${act}</div>` : ''}
      </div>
      ${risk.tradeGrade && risk.tradeGrade !== '—' ? `
      <div class="verdict-grade">
        <div class="verdict-conf-label">Trade Grade</div>
        <div class="verdict-grade-big" style="color:${gradeColor};">${risk.tradeGrade}</div>
      </div>` : ''}
    </div>
    <div class="verdict-strip" style="grid-template-columns:repeat(4,minmax(0,1fr));">
      <div class="verdict-stat">
        <div class="k">PCR</div><div class="v" title="${vrd.pcr || ''}">${shortVal(vrd.pcr)}</div>
        ${explainVal(vrd.pcr) ? `<div class="verdict-stat-explain" title="${vrd.pcr}">${explainVal(vrd.pcr)}</div>` : ''}
      </div>
      <div class="verdict-stat verdict-stat-2line">
        <div class="verdict-stat-line"><div class="k">India VIX</div><div class="v" title="${vrd.vix || ''}">${shortVal(vrd.vix)}</div></div>
        <div class="verdict-stat-line"><div class="k">IV Regime</div><div class="v" style="color:${ivRgColor};">${risk.ivRegime || '—'}</div></div>
        ${explainVal(vrd.vix) ? `<div class="verdict-stat-explain" title="${vrd.vix}">${explainVal(vrd.vix)}</div>` : ''}
      </div>
      <div class="verdict-stat verdict-stat-2line">
        <div class="verdict-stat-line"><div class="k">Max Pain</div><div class="v">${d.maxPain!=null?fmtI(d.maxPain):'—'}</div></div>
        <div class="verdict-stat-line"><div class="k">ATM Strike</div><div class="v">${atm?fmtI(atm):'—'}</div></div>
        <!-- No vrd.maxPain narrative field exists in the payload today —
             this hook is wired for when/if the backend adds one, and
             renders nothing in the meantime rather than fabricating text. -->
        ${explainVal(vrd.maxPain) ? `<div class="verdict-stat-explain" title="${vrd.maxPain}">${explainVal(vrd.maxPain)}</div>` : ''}
      </div>
      <div class="verdict-stat verdict-stat-2line">
        <div class="oic-tile" style="padding:0;border:0;background:transparent;">
          ${wallBuild.ceStrike!==null ? `
          <div class="oic-build-row">
            <span>CE Wall</span>
            <span><button class="strike-link ce" onclick="event.stopPropagation();openOptionChainAtStrike(${wallBuild.ceStrike})">${fmtI(wallBuild.ceStrike)}</button><span class="delta up">▲${fmtK(wallBuild.ceVal)}</span></span>
          </div>` : ''}
          ${wallBuild.peStrike!==null ? `
          <div class="oic-build-row">
            <span>PE Wall</span>
            <span><button class="strike-link pe" onclick="event.stopPropagation();openOptionChainAtStrike(${wallBuild.peStrike})">${fmtI(wallBuild.peStrike)}</button><span class="delta up">▲${fmtK(wallBuild.peVal)}</span></span>
          </div>` : ''}
          ${wallBuild.ceStrike===null && wallBuild.peStrike===null ? '<div class="oic-empty">—</div>' : ''}
        </div>
        <!-- ₹ Wall = highest-PREMIUM strike (oi.capital_metrics'
             ce_capital_wall_strike/pe_capital_wall_strike — OI x LTP), as
             opposed to CE/PE Wall above (highest raw OI). Always shown
             (not just on divergence) so the capital-weighted view is
             visible on the dashboard at a glance; when it matches the
             raw-OI wall that's itself useful confirmation, not noise. -->
        ${(() => {
          const capCe = d.capitalCeWallStrike, capPe = d.capitalPeWallStrike;
          if (!capCe && !capPe) return '';
          return `<div class="oic-tile" style="padding:0;border:0;background:transparent;margin-top:2px;" title="Highest premium-locked strike — OI x LTP, can differ from the raw-OI wall above">
            ${capCe ? `<div class="oic-build-row"><span>₹ CE Wall</span><button class="strike-link ce" onclick="event.stopPropagation();openOptionChainAtStrike(${capCe})">${fmtI(capCe)}</button></div>` : ''}
            ${capPe ? `<div class="oic-build-row"><span>₹ PE Wall</span><button class="strike-link pe" onclick="event.stopPropagation();openOptionChainAtStrike(${capPe})">${fmtI(capPe)}</button></div>` : ''}
          </div>`;
        })()}
        ${(() => {
          const wallExplain = [explainVal(vrd.ceWall), explainVal(vrd.peWall)].filter(Boolean).join(' · ');
          const wallTitle = [vrd.ceWall, vrd.peWall].filter(Boolean).join(' · ');
          return wallExplain ? `<div class="verdict-stat-explain" title="${wallTitle}">${wallExplain}</div>` : '';
        })()}
      </div>
    </div>
  </div>

  <!-- ── DECISION DETAIL — Tier-3 collapsible ── -->
  <details class="card" id="decision-detail-card" style="margin-bottom:10px;" ${detailOpen ? 'open' : ''}>
    <summary>
      <div class="card-head"><span class="ic">🧭</span>Decision Detail</div>
      <span class="chev">▶</span>
    </summary>
    <div class="detail-body">

      <!-- Active Signals (left) + S & R Levels (right), 2-column grid
           (.dd-grid, panels.css). Trap Warning used to be its own
           full-width strip above this grid; it's folded into the S&R
           Levels column instead — it originally lived in the Risk
           Dashboard section alongside key levels before that section was
           removed (see chain-renderer.js), so this puts it back next to
           the numbers it's warning about rather than floating above
           both columns on its own. Only rendered when there's an actual
           warning, same gating as before.

           Both columns are wrapped so they stretch to the row's full
           height and center their content within it (dd-sig-list on the
           left, metric-strip on the right already did this) — on a busy tick
           Active Signals can run 4-5 rows deep and S&R Levels (now
           carrying the trap strip too) needs to match that height rather
           than leaving the shorter side looking sparse. -->
      <div class="dd-grid">
        <div class="dd-col">
          <div class="dd-col-title">Active Signals</div>
          <div class="dd-sig-list">
            ${sigs.length ? sigs.map(s=>`
              <div class="dd-sig">
                <span style="color:${sevClr(s.severity)};font-weight:700;flex-shrink:0;">${sevDot(s.severity)}</span>
                <span style="color:${s.severity==='warn'||s.severity==='ok'?'var(--text-primary)':'var(--text-tertiary)'};">${s.text}</span>
              </div>`).join('') : '<div class="dd-empty">No active signals.</div>'}
          </div>
        </div>

        <div class="dd-col">
          <div class="dd-col-title">S &amp; R Levels</div>
          ${hasTrapWarn ? `
          <div class="dd-trap">
            <span class="ic">\u26A0</span>
            <span class="lbl">Trap Warning</span>
            <span class="txt" title="${risk.trapWarn}">${risk.trapWarn}</span>
          </div>` : ''}
          ${(()=>{
            const r1   = d.ceWall || 0;
            const s1   = d.peWall || 0;
            const step = d.strikeStep || 200;
            const r2   = r1 + step;
            const s2   = s1 - step;
            const spot = Number(d.spot) || 0;
            // Points-away-from-spot, sign-prefixed — the same
            // "+123"/"-123" convention panels-views.js's rupee() helper
            // uses. Resistance levels sit above spot (bear-colored),
            // support below (bull-colored) — flip which color reads as
            // "closer" vs "further" would be backwards, so the dist line
            // keeps the same bull/bear class as its parent cell rather
            // than a directional color of its own.
            const dist = lvl => spot ? `${lvl>=spot?'+':''}${fmtI(Math.round(lvl-spot))}` : '';
            // Split atmIvTxt's "13.0% · IV Rank 35" back into its two
            // parts (was joined with ' · ' above) so each half gets its
            // own line instead of both fighting for one line's width.
            const [ivMain, ivSub] = atmIvTxt ? atmIvTxt.split(' · ') : [null, null];
            return `<div class="metric-strip">
              <div class="metric-cell"><div class="k">R2</div><div class="v bear">${fmtI(r2)}</div><div class="d bear">${dist(r2)}</div></div>
              <div class="metric-cell"><div class="k">R1</div><div class="v bear">${fmtI(r1)}</div><div class="d bear">${dist(r1)}</div></div>
              <div class="metric-cell"><div class="k">S1</div><div class="v bull">${fmtI(s1)}</div><div class="d bull">${dist(s1)}</div></div>
              <div class="metric-cell"><div class="k">S2</div><div class="v bull">${fmtI(s2)}</div><div class="d bull">${dist(s2)}</div></div>
              ${ivMain ? `<div class="metric-cell"><div class="k">ATM IV</div><div class="v">${ivMain}</div>${ivSub ? `<div class="d">${ivSub}</div>` : ''}</div>` : ''}
            </div>`;
          })()}
        </div>
      </div>

    </div>
  </details>
</div>`;
};

  // Compact "Option Chain Snapshot" card — sits between the Executive
  // boxes and OI Flow (see renderDashboard below). This was previously
  // only a comment/placeholder (#chain-anchor expected a static #sec-chain
  // block to be moved into it, but that block was removed from
  // DashboardPro.html) — nothing was ever actually built here. The full
  // strike-by-strike ledger (Greeks toggle, buy/sell click cells, Bid/Ask
  // depth) still lives at option-chain.html; this card is just the ATM
  // read plus a link there.
ChainView.prototype.buildChainSummaryHtml = function(d) {
  const chain = getFilteredChain(d);

  if(!chain.length){
    return `
  <div class="section-card sc-green" id="chain-summary-card">
    <button class="section-header nav-card-header" onclick="openOptionChain()"
       aria-label="Open Option Chain Snapshot — view full option chain"
       title="Open full option chain">
      <span class="section-title nav-card-header-label"><span class="section-icon">📊</span>Option Chain Snapshot</span>
      <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
    </button>
    <div class="dd-empty">Awaiting chain data…</div>
  </div>`;
  }

  // Unit-aware K/L/Cr formatter on the RAW number (not pre-scaled) — same
  // approach as option-chain.js's fmt(), which this card is modeled on.
  // chain-views.js's own global fmt()/fmtK() stop at "L" and never scale
  // to "Cr", so a separate helper is needed here to match that reference
  // layout's units exactly.
  const fmtCrLK = (v) => {
    if(v==null||isNaN(v)) return '—';
    const a = Math.abs(v);
    const s = v<0 ? '-' : '';
    if(a>=1e7) return s+(a/1e7).toFixed(2)+'Cr';
    if(a>=1e5) return s+(a/1e5).toFixed(2)+'L';
    if(a>=1e3) return s+(a/1e3).toFixed(1)+'K';
    return s+a.toFixed(0);
  };
  const signedFmt = (v) => (v>0?'+':'') + fmtCrLK(v);
  // signColor()'s default neutral is already --text-primary, matching the
  // reference mockup's "0 stays bold/white, not greyed out" behavior.

  // Decorative trend line for the two top stat cards. There's no
  // continuous net-OI time-series in the payload to plot a real
  // sparkline against — this only encodes direction (up/down) via the
  // sign of the figure it sits beside, not actual magnitude-over-time.
  // Swap `pts` for a real series (e.g. a rolling net-OI history array)
  // if/when one gets added to the payload.
  const buildSparkline = (colorVar, up) => {
    const pts = up ? "2,32 20,29 38,24 56,27 74,15 96,5" : "2,5 20,13 38,10 56,22 74,19 96,32";
    const [lx, ly] = pts.split(' ').pop().split(',');
    return `<svg width="98" height="38" viewBox="0 0 98 38" class="oi-snap-spark">
      <polyline points="${pts}" fill="none" stroke="${colorVar}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${lx}" cy="${ly}" r="3.5" fill="${colorVar}"/>
    </svg>`;
  };
  // ── Positioning summary ─────────────────────────────────────────
  // D-04 owns aggregate positioning only. Intraday OI/capital FLOW has
  // moved to D-07 so this card answers one question: where is positioning
  // concentrated across the currently visible strike range?
  const {totalCe, totalPe, pcr} = computeRangeChainTotals(chain);

  const totalCeChg = chain.reduce((s,r)=>s+(r.ceChgOI||0),0);
  const totalPeChg = chain.reduce((s,r)=>s+(r.peChgOI||0),0);
  const prevCe = totalCe-totalCeChg, prevPe = totalPe-totalPeChg;
  const prevPcr = prevPe/(prevCe||1);
  const pcrShift = pcr-prevPcr;

  const netOi = totalPe-totalCe;
  const netChgOi = totalPeChg-totalCeChg;

  const totalCeVol = chain.reduce((sum,r)=>sum+(r.ceVol||0),0);
  const totalPeVol = chain.reduce((sum,r)=>sum+(r.peVol||0),0);
  const ceVolOi = totalCeVol/(totalCe||1);
  const peVolOi = totalPeVol/(totalPe||1);

  const rngLabel = (() => { const rng = typeof _chainRange !== 'undefined' ? _chainRange : 10; return rng===9999?'ALL STRIKES':'±'+rng+' STRIKES'; })();
  const rngTag = (() => { const rng = typeof _chainRange !== 'undefined' ? _chainRange : 10; return rng===9999?'All':'±'+rng; })();

  return `
  <div class="section-card sc-green" id="chain-summary-card">
    <button class="section-header nav-card-header" onclick="openOptionChain()"
       aria-label="Open Option Chain Snapshot — view full option chain"
       title="Open full option chain">
      <span class="oi-snap-heading nav-card-header-label">
        <svg width="20" height="16" viewBox="0 0 20 16" fill="none"><rect x="0" y="8" width="4" height="8" rx="1" fill="var(--neg)"/><rect x="6" y="4" width="4" height="12" rx="1" fill="var(--warn)"/><rect x="12" y="0" width="4" height="16" rx="1" fill="var(--pos)"/></svg>
        Option Chain Snapshot
      </span>
      <span class="oi-snap-badge">${rngLabel}</span>
      <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
    </button>

    <div class="oi-snap-grid">

      <div class="oi-snap-card pos">
        <div class="oi-snap-card-top">
          <div class="oi-snap-icon pos">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--pos)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>
          </div>
          <div class="oi-snap-title">OI Summary</div>
        </div>
        <div class="oi-snap-body">
          <div>
            <div class="oi-snap-label">Net OI</div>
            <div class="oi-snap-value ${netOi>=0?'pos':'neg'}">${signedFmt(netOi)}</div>
          </div>
          ${buildSparkline('var(--pos)', netOi>=0)}
        </div>
        <div class="oi-snap-footer">
          <span class="oi-snap-footer-label">Range PCR <span style="font-weight:400;text-transform:none;">(${rngTag})</span></span>
          <span class="oi-snap-footer-val warn">${fmtN(pcr,2)}</span>
          <span class="oi-snap-footer-tag">Put Call Ratio, ${rngLabel.toLowerCase()}</span>
        </div>
      </div>

      <div class="oi-snap-card info">
        <div class="oi-snap-card-top">
          <div class="oi-snap-icon info">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--info)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 5"/><polyline points="15 5 21 5 21 11"/></svg>
          </div>
          <div class="oi-snap-title">Chg OI Summary</div>
        </div>
        <div class="oi-snap-body">
          <div>
            <div class="oi-snap-label">Net OI Δ</div>
            <div class="oi-snap-value ${netChgOi>=0?'pos':'neg'}">${signedFmt(netChgOi)}</div>
          </div>
          ${buildSparkline('var(--info)', netChgOi>=0)}
        </div>
        <div class="oi-snap-footer">
          <span class="oi-snap-footer-label">Range PCR Δ</span>
          <span class="oi-snap-footer-val warn">${signedFmt(pcrShift)}</span>
          <span class="oi-snap-footer-tag">Change in Range PCR</span>
        </div>
      </div>

    </div>

    <div class="oi-snap-kpis" aria-label="Option chain positioning metrics">
      <div class="oi-snap-kpi">
        <span class="oi-snap-kpi-label">Max Pain</span>
        <strong class="oi-snap-kpi-value">${d.maxPain!=null?fmtI(d.maxPain):'—'}</strong>
      </div>
      <div class="oi-snap-kpi">
        <span class="oi-snap-kpi-label">CE Vol/OI</span>
        <strong class="oi-snap-kpi-value ce">${fmtN(ceVolOi,2)}x</strong>
      </div>
      <div class="oi-snap-kpi">
        <span class="oi-snap-kpi-label">PE Vol/OI</span>
        <strong class="oi-snap-kpi-value pe">${fmtN(peVolOi,2)}x</strong>
      </div>
    </div>
  </div>`;
};

  // dOI · 5/15/30m detail card remains removed. Intraday flow now belongs
  // to D-07 (Vol/OI Velocity + OI Flow), not this D-04 positioning card.
  // Its old mount point remains intentionally absent from row3.

  // ── Volume & Vol/OI DETAIL (Tier-3 collapsible) ──
  // Same treatment as buildDoiDetailHtml above: previously the 3rd
  // arp-sum-card inside buildChainSummaryHtml, moved out to its own
  // collapsible card so the always-visible Snapshot grid shows 2 cards
  // (OI Summary, Chg OI Summary) instead of 3. Same calculation and
  // arp-vratio-* markup as before, just relocated + collapsed by default;
  // fmtCrLK is duplicated from buildChainSummaryHtml/buildDoiDetailHtml
  // rather than shared, for the same self-contained-render-function reason
  // noted on buildDoiDetailHtml.
ChainView.prototype.buildVolOiDetailHtml = function(d) {
  const chain = getFilteredChain(d);
  if(!chain.length) return '';

  const fmtCrLK = (v) => {
    if(v==null||isNaN(v)) return '—';
    const a = Math.abs(v);
    const s = v<0 ? '-' : '';
    if(a>=1e7) return s+(a/1e7).toFixed(2)+'Cr';
    if(a>=1e5) return s+(a/1e5).toFixed(2)+'L';
    if(a>=1e3) return s+(a/1e3).toFixed(1)+'K';
    return s+a.toFixed(0);
  };

  // computeRangeChainTotals (metrics.js, IA redesign step 6) — same OI
  // totals as buildChainSummaryHtml above; only totalCe/totalPe are used
  // here (as vol-ratio denominators), .pcr is unused in this function.
  const {totalCe, totalPe} = computeRangeChainTotals(chain);
  const totalCeVol = chain.reduce((s,r)=>s+(r.ceVol||0),0);
  const totalPeVol = chain.reduce((s,r)=>s+(r.peVol||0),0);
  const ratioCap = 3;
  const ceRatio = totalCeVol/(totalCe||1);
  const peRatio = totalPeVol/(totalPe||1);

  return `
  <details class="card" id="voloi-detail-card">
    <summary>
      <div class="card-head"><span class="ic">📶</span>Volume &amp; Vol/OI</div>
      <span class="chev">▶</span>
    </summary>
    <div class="detail-body">
      <div class="arp-vratio-rows">
        <div class="arp-vratio-row">
          <span class="arp-vratio-side" style="color:var(--neg);">CE</span>
          <span class="arp-vratio-num" style="color:var(--neg);">${fmtCrLK(totalCeVol)}</span>
          <div class="arp-vratio-track"><div class="arp-vratio-fill ce" style="width:${Math.min(100,(ceRatio/ratioCap)*100)}%;"></div></div>
          <span class="arp-vratio-val">${fmtN(ceRatio,2)}x</span>
        </div>
        <div class="arp-vratio-row">
          <span class="arp-vratio-side" style="color:var(--pos);">PE</span>
          <span class="arp-vratio-num" style="color:var(--pos);">${fmtCrLK(totalPeVol)}</span>
          <div class="arp-vratio-track"><div class="arp-vratio-fill pe" style="width:${Math.min(100,(peRatio/ratioCap)*100)}%;"></div></div>
          <span class="arp-vratio-val">${fmtN(peRatio,2)}x</span>
        </div>
      </div>
      <div class="legend-foot"><span><b>Vol/OI</b> today's traded volume divided by open interest, summed across visible strikes</span></div>
    </div>
  </details>`;
};


  // ── FULL IV SURFACE (modal content) ──
  // NOTE: the old standalone "IV Surface" alerts card (buildIvAlertsHtml)
  // that used to render here into its own always-visible #sec-iv section
  // has been removed — its alerts and "Full Surface →" link now live
  // inside chain-greeks.js's buildIvHvSkewDetailHtml (the Tier-3
  // "IV vs HV / Skew" collapsible), since that card had no role of its
  // own beyond "No IV alerts right now" most of the time.
  // Same per-strike CE/PE bar table + Skew/Max IV/Min IV footer that used
  // to render inline in the main template. Pulled out into its own method
  // so it can be (a) written once into the modal's static content div and
  // (b) refreshed from that same place on every tick / expiry switch via
  // renderIvSurfaceModal() below, instead of duplicating this markup in
  // both the initial template and the incremental-refresh path.
  //
  // SIZE FIX: the previous version bumped bar height/font-size to near-
  // double the original compact mockup to fix a "too-tiny" complaint, but
  // that overcorrected — full-width modal rows ballooned to ~90px each.
  // Pulled padding/gap/bar-height/font-size back down to a middle ground
  // (still bigger than the original 8px/9-10px cramped version, well
  // short of the 15px/1rem oversized one) so more strikes are visible per
  // screen without scrolling.
ChainView.prototype.buildIvSurfaceHtml = function(d, chain, atm) {
  // BUGFIX: this used to ignore the `chain` array it was given (which
  // getFilteredChain() already filtered to the globally-selected range)
  // and re-sliced its own fixed ATM±3 window every time — so the ±3/±5/
  // ±10/±15/All buttons in the modal's header visibly highlighted but the
  // row count never changed. `chain` is already the right window; just
  // use it directly.
  const ivRows = chain;
  const maxIV = Math.max(...ivRows.map(r => Math.max(r.ceIV||0, r.peIV||0)), 1);

  const headerHtml = `<div style="display:grid;grid-template-columns:1fr 84px 1fr;align-items:center;gap:8px;padding:0 10px 6px;border-bottom:1px solid var(--border);margin-bottom:4px;">
      <div style="text-align:right;font-size:0.6875rem;font-weight:700;color:var(--ce);text-transform:uppercase;letter-spacing:.06em;">CE IV</div>
      <div style="text-align:center;font-size:0.6875rem;font-weight:700;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.06em;">Strike</div>
      <div style="text-align:left;font-size:0.6875rem;font-weight:700;color:var(--pe);text-transform:uppercase;letter-spacing:.06em;">PE IV</div>
    </div>`;

  let rowsHtml = '';
  ivRows.forEach(r => {
    const ia = r.atm || r.strike === atm;
    const ceIV = r.ceIV || 0;
    const peIV = r.peIV || 0;
    const ceWidthPct = Math.max((ceIV / maxIV) * 100, 3);
    const peWidthPct = Math.max((peIV / maxIV) * 100, 3);
    rowsHtml += `<div style="display:grid;grid-template-columns:1fr 84px 1fr;align-items:center;gap:8px;padding:2px 10px;${ia?'background:rgba(18,184,134,0.08);border-radius:6px;':''}">
      <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;">
        <span style="font-size:0.75rem;font-family:var(--mono);color:var(--ce);font-weight:600;white-space:nowrap;min-width:44px;text-align:right;">${fmtN(ceIV,2)}%</span>
        <div style="flex:1 1 auto;min-width:0;display:flex;justify-content:flex-end;">
          <div style="height:7px;border-radius:2px 0 0 2px;background:var(--ce);width:${ceWidthPct}%;min-width:4px;"></div>
        </div>
      </div>
      <div style="text-align:center;padding:0 4px;">
        <span style="font-family:var(--mono);font-size:0.75rem;font-weight:${ia?700:500};color:${ia?'var(--pos)':'var(--text-secondary)'};white-space:nowrap;">${fmtI(r.strike)}${ia?' ★':''}</span>
      </div>
      <div style="display:flex;align-items:center;justify-content:flex-start;gap:6px;">
        <div style="flex:1 1 auto;min-width:0;">
          <div style="height:7px;border-radius:0 2px 2px 0;background:var(--pe);width:${peWidthPct}%;min-width:4px;"></div>
        </div>
        <span style="font-size:0.75rem;font-family:var(--mono);color:var(--pe);font-weight:600;white-space:nowrap;min-width:44px;">${fmtN(peIV,2)}%</span>
      </div>
    </div>`;
  });
  const minIV = Math.min(...ivRows.map(r => Math.min(r.ceIV||0, r.peIV||0)));

  return `<div style="display:flex;flex-direction:column;">${headerHtml}<div style="display:flex;flex-direction:column;gap:2px;">${rowsHtml}</div></div>
    <div style="font-size:0.8125rem;color:var(--text-tertiary);margin-top:10px;padding-top:10px;border-top:1px solid var(--border);display:flex;gap:24px;flex-wrap:wrap;">
      <span>Skew <strong style="color:var(--warn);">${fmtN(d.atmSkew,2)}%</strong> at ATM</span>
      <span>Max IV <strong style="color:var(--ce);">${fmtN(maxIV,2)}%</strong></span>
      <span>Min IV <strong style="color:var(--pe);">${fmtN(minIV,2)}%</strong></span>
    </div>`;
};