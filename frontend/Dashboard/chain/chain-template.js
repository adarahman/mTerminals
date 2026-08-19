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

// DATA SOURCE options — the seven runtime market-data providers
// (SMARTAPI/UPSTOX/KITE/SHOONYA/BREEZE/KOTAK/NSE_BSE) from the backend's
// d.dataSources [{id,label,status,active,capabilities}]. The select text
// stays compact (short label only) so the control doesn't dominate the
// top bar; each provider's status (LIVE/POLLING/UNAVAILABLE/
// SESSION_REQUIRED) rides in the option tooltip and is also shown
// live by the separate data-source-status-pill next to the select. The
// active one is pre-selected via the backend's `active` flag.
ChainView.prototype.renderDataSourceOptions = function(sources, active) {
  if (!Array.isArray(sources) || !sources.length) {
    const a = active || 'NSE_BSE';
    return `<option value="${a}">${escapeHtml(a)}</option>`;
  }
  return sources.map(s => {
    const id = s.id;
    const label = s.label || id;
    return `<option value="${id}"${s.active?' selected':''} title="Status: ${escapeHtml(s.status||'')}">${escapeHtml(label)}</option>`;
  }).join('');
};

ChainView.prototype.renderTopBarHtml = function(d, isBear) {
  const feedState = (window.AppState && AppState.feedState) || {status:'CONNECTING'};
  const rawFeedStatus = feedState.status || 'CONNECTING';
  const marketSession = feedState.marketSession || 'UNKNOWN';
  const feedReasonRaw = feedState.reason
    || (feedState.quality === 'PARTIAL' && Array.isArray(feedState.missing) && feedState.missing.length
      ? `Missing: ${feedState.missing.join(', ')}` : '');
  const feedReason = escapeHtml(feedReasonRaw);
  let feedLabel = rawFeedStatus;
  let feedStatus = rawFeedStatus.toLowerCase();
  if (marketSession === 'HOLIDAY') {
    feedLabel = rawFeedStatus === 'DISCONNECTED' ? 'HOLIDAY · OFFLINE' : 'HOLIDAY';
    feedStatus = 'holiday';
  } else if (marketSession === 'MARKET_CLOSED') {
    feedLabel = rawFeedStatus === 'DISCONNECTED' ? 'MARKET CLOSED · OFFLINE' : 'MARKET CLOSED';
    feedStatus = 'market-closed';
  } else if (rawFeedStatus === 'LIVE' && feedState.quality === 'PARTIAL') {
    feedLabel = 'PARTIAL';
    feedStatus = 'partial';
  }
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
      <span class="price-source-tag" title="EQ is the fixed option-pricing and decision reference">EQ</span>
      <span class="spot-block">
        <span id="topbar-spot" class="spot${isBear?' bearish':''}${spotFlashCls}">${fmtI(d.spot)}</span>
        ${d.spotChgPct!==undefined?`
        <span class="spot-change-row">
          <span id="spot-chg-pts" class="chg-token ${d.spotChgPct>=0?'chg-pos':'chg-neg'}" title="Change in points">${d.spotChange>=0?'+':''}${Math.round(d.spotChange||0)}</span>
          <span id="spot-chg-pct" class="chg-token ${d.spotChgPct>=0?'chg-pos':'chg-neg'}">${d.spotChgPct>=0?'▲':'▼'} ${Math.abs(d.spotChgPct).toFixed(2)}%</span>
        </span>`:''}
      </span>
      ${renderIndexTicker(d)}
    </div>
    <div class="expiry-strip">
      <div class="expiry-pill feed-health-pill">
        <span class="feed-row">
          <span class="expiry-pill-label">Feed</span>
          <span class="feed-status-pill" id="feed-status-pill" data-status="${feedStatus}" title="${feedReason}">${feedLabel}</span>
        </span>
        <!-- DATA SOURCE (broker) dropdown — sits on its own aligned row
             below the feed status. The runtime market-data provider
             (ANGEL ONE/UPSTOX/SHOONYA/ZERODHA/ICICI DIRECT/KOTAK NEO/NSE/BSE);
             picking one
             reconnects the WS with ?dataSource=... (see
             switchDataSource() in market-context.js); the backend applies
             it process-wide on the next engine_loop tick via
             switch_data_source() — no server restart. Per-provider status
             lives in each option's tooltip; the active provider's live
             state (LIVE/POLLING/UNAVAILABLE/SESSION_REQUIRED) is the small
             pill to its right. -->
        <span class="feed-row">
          <select id="dataSourceSelect" class="price-source-select data-source-select" title="Market-data source — runtime switch, no server restart" onchange="onDataSourcePicked(this.value)">${this.renderDataSourceOptions(d.dataSources, d.dataSource)}</select>
          ${(() => {
            const list = Array.isArray(d.dataSources) ? d.dataSources : [];
            const active = list.find(s => s.active) || (d.dataSource ? list.find(s => s.id === d.dataSource) : null) || {};
            const st = active.status || '';
            // Tiny plain borderless readout (see .data-source-status-pill in
            // navigation.css) — the dropdown itself is the primary signal.
            return st ? `<span class="feed-status-pill data-source-status-pill" data-status="${String(st).toLowerCase()}" title="Active market-data source status">${escapeHtml(st)}</span>` : '';
          })()}
        </span>
      </div>
      <div class="expiry-divider"></div>
      <!-- Expiry is its own dedicated pill, separate from DTE, and sits
           leftmost in the strip. The same persistent <select> node from
           #expiry-select-holder is re-parented into #expiry-slot on every
           render (see moveExpirySelectIntoTopBar()) rather than rebuilt,
           so its option list and current value survive live ticks. -->
      <div class="expiry-pill">
        <span class="expiry-row">
          <span class="expiry-pill-label">Expiry</span>
          <span class="expiry-dte" id="dte-display">${(d.dte||0)}d</span>
        </span>
        <span class="expiry-row"><span id="expiry-slot"></span></span>
      </div>
      <div class="expiry-divider"></div>
      <div class="expiry-pill">
        <span class="expiry-pill-label">Updated</span>
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
  // in paper-trading-shared.js is still the source of that data — it's just
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
    const MINI_CHART_DRAW_POINTS = 150;
    const MINI_CHART_BUFFER_POINTS = 2500;
    let chartPrefs = { range: '1m', windowKey: '1D' };
    try {
      chartPrefs = Object.assign(chartPrefs, JSON.parse(localStorage.getItem('priceChartSettings.v2') || '{}'));
    } catch (_) { /* retain the first-use chart defaults */ }
    const prefsSignature = `${chartPrefs.range}|${chartPrefs.windowKey || 'custom'}|${chartPrefs.type || 'line'}`;
    if (!this._miniChartHistory || (d.symbol && d.symbol !== this._miniChartSymbol) || this._miniChartPrefsSignature !== prefsSignature) {
      this._miniChartHistory = [];
      this._miniChartSymbol = d.symbol;
      this._miniChartPrefsSignature = prefsSignature;
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
      // Respect the interval selected in the full Price Chart. Previously
      // every live websocket price became a brand-new mini-chart point,
      // so a selected 5m candle marched horizontally every second. Keep
      // one fixed timestamp per interval bucket and update that candle's
      // H/L/C in place; only the first tick of the next bucket advances X.
      const intervalMs = {
        '1m': 60 * 1000,
        '5m': 5 * 60 * 1000,
        '15m': 15 * 60 * 1000,
        '1h': 60 * 60 * 1000,
        '1d': 24 * 60 * 60 * 1000,
      }[chartPrefs.range] || 60 * 1000;
      const bucketStart = Math.floor(Date.now() / intervalMs) * intervalMs;
      if (last && last.t === bucketStart) {
        const open = Number.isFinite(last.o) ? last.o : last.p;
        const high = Number.isFinite(last.h) ? last.h : last.p;
        const low = Number.isFinite(last.l) ? last.l : last.p;
        last.o = open;
        last.h = Math.max(high, spotNum);
        last.l = Math.min(low, spotNum);
        last.c = spotNum;
        last.p = spotNum;
      } else if (!last || last.t < bucketStart) {
        hist.push({
          t: bucketStart,
          o: spotNum,
          h: spotNum,
          l: spotNum,
          c: spotNum,
          p: spotNum,
        });
        if (hist.length > MINI_CHART_BUFFER_POINTS) hist.shift();
      }
    }

    // ── BACKFILL FROM REAL HISTORY ──
    // This buffer is pure in-memory tick accumulation — it starts empty on
    // every page load/refresh and only grows as live spot ticks arrive. On
    // a non-trading session (after close, weekend, holiday) no tick ever
    // changes, so a fresh load shows nothing but the flat dashed
    // placeholder forever, even though the backend's own /api/history
    // (the same endpoint price-chart-engine.js's history-loader.js already uses)
    // has the last real session's candles sitting right there. Same
    // principle as that chart's render(): show the last real session's
    // shape frozen rather than an empty/placeholder trace. Guarded by
    // _miniChartHydratedSymbol/_miniChartHydrating so this fires once per
    // symbol, not on every render call.
    if (this._miniChartHistory.length < 2 && !this._miniChartHydrating && this._miniChartHydratedSymbol !== d.symbol) {
      this._miniChartHydrating = true;
      const symForFetch = d.symbol || 'NIFTY';
      const prefsForFetch = prefsSignature;
      (window.fetchMarketHistory
        ? window.fetchMarketHistory(symForFetch, chartPrefs.range)
        : fetch(`${Config.api.history}?symbol=${encodeURIComponent(symForFetch)}&range=${encodeURIComponent(chartPrefs.range)}`).then(res => res.ok ? res.json() : []))
        .then(rows => {
          // Bail if the symbol moved on again while this was in flight, or
          // a live tick already beat the fetch back and started filling
          // the real buffer — don't clobber newer data with a stale fetch.
          if (this._miniChartSymbol !== symForFetch || this._miniChartPrefsSignature !== prefsForFetch || this._miniChartHistory.length >= 2) return;
          if (Array.isArray(rows) && rows.length) {
            const bars = rows
              .map(r => ({
                t: Number(r.t), o: parseFloat(r.o), h: parseFloat(r.h),
                l: parseFloat(r.l), c: parseFloat(r.c), p: parseFloat(r.c)
              }))
              .filter(r => Number.isFinite(r.t) && Number.isFinite(r.p));
            if (bars.length) this._miniChartHistory = bars.slice(-MINI_CHART_BUFFER_POINTS);
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

    let pts = this._miniChartHistory;
    const windowMs = {
      '5D': 5 * 86400000, '1M': 30 * 86400000, '3M': 90 * 86400000,
      '6M': 182 * 86400000, '1Y': 365 * 86400000, '5Y': 5 * 365 * 86400000
    }[chartPrefs.windowKey];
    if (pts.length && windowMs) {
      const end = pts[pts.length - 1].t;
      pts = pts.filter(p => p.t >= end - windowMs);
    } else if (pts.length && chartPrefs.windowKey === '1D') {
      const istDay = t => new Intl.DateTimeFormat('en-CA', { timeZone:'Asia/Kolkata', year:'numeric', month:'2-digit', day:'2-digit' }).format(new Date(t));
      const lastDay = istDay(pts[pts.length - 1].t);
      pts = pts.filter(p => istDay(p.t) === lastDay);
    }
    // The dashboard preview is intentionally a close-up, not a miniature
    // copy of the whole session. For intraday intervals show only the most
    // recent 90 trading minutes so individual 1m/5m/15m candles remain
    // readable in the compact card. The full modal still shows the complete
    // selected window (for example the entire 1D session).
    if (pts.length && ['1m', '5m', '15m'].includes(chartPrefs.range)) {
      const previewEnd = pts[pts.length - 1].t;
      pts = pts.filter(p => p.t >= previewEnd - 90 * 60 * 1000);
    }
    // Preserve the complete selected span while keeping the tiny SVG light.
    // Candle mode aggregates each pixel group so its high/low is retained.
    const drawLimit = chartPrefs.type === 'candle' ? 55 : MINI_CHART_DRAW_POINTS;
    if (pts.length > drawLimit && chartPrefs.type === 'candle') {
      const source = pts;
      pts = Array.from({length: drawLimit}, (_, i) => {
        const from = Math.floor(i * source.length / drawLimit);
        const to = Math.max(from + 1, Math.floor((i + 1) * source.length / drawLimit));
        const group = source.slice(from, to);
        const value = (p, key) => Number.isFinite(p[key]) ? p[key] : p.p;
        return {
          t: group[group.length - 1].t,
          o: value(group[0], 'o'), c: value(group[group.length - 1], 'c'),
          h: Math.max(...group.map(p => value(p, 'h'))),
          l: Math.min(...group.map(p => value(p, 'l')))
        };
      });
    } else if (pts.length > drawLimit) {
      const source = pts;
      pts = Array.from({length: drawLimit}, (_, i) => source[Math.round(i * (source.length - 1) / (drawLimit - 1))]);
    }
    const W = 280, H = 90, PAD = 6;
    let svgInner;
    if (pts.length < 2) {
      // Not enough ticks yet for a meaningful trace — flat placeholder
      // line rather than an empty box, so the widget's footprint/click
      // target is identical from the very first render.
      svgInner = `<line x1="${PAD}" y1="${H/2}" x2="${W-PAD}" y2="${H/2}" stroke="var(--text-tertiary)" stroke-width="2.5" stroke-dasharray="4,4"/>`;
    } else if (chartPrefs.type === 'candle') {
      const value = (p, key) => Number.isFinite(p[key]) ? p[key] : p.p;
      const lows = pts.map(p => value(p, 'l'));
      const highs = pts.map(p => value(p, 'h'));
      const min = Math.min(...lows), max = Math.max(...highs);
      const span = Math.max(max - min, ((max + min) / 2) * 0.0015) || 1;
      const yMin = (max + min) / 2 - span / 2;
      const y = v => PAD + (H - PAD * 2) * (1 - (v - yMin) / span);
      const stepX = (W - PAD * 2) / pts.length;
      const bodyW = Math.max(1.5, Math.min(4, stepX * 0.65));
      const safeColor = (v, fallback) => /^#[0-9a-f]{6}$/i.test(v || '') ? v : fallback;
      const upColor = safeColor(chartPrefs.upColor, '#26A69A');
      const downColor = safeColor(chartPrefs.downColor, '#EF5350');
      svgInner = pts.map((p, i) => {
        const o = value(p, 'o'), h = value(p, 'h'), l = value(p, 'l'), c = value(p, 'c');
        const x = PAD + (i + 0.5) * stepX;
        const color = c >= o ? upColor : downColor;
        const bodyTop = Math.min(y(o), y(c));
        const bodyH = Math.max(1, Math.abs(y(c) - y(o)));
        return `<line x1="${x.toFixed(1)}" y1="${y(h).toFixed(1)}" x2="${x.toFixed(1)}" y2="${y(l).toFixed(1)}" stroke="${color}" stroke-width="1"/><rect x="${(x-bodyW/2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${bodyH.toFixed(1)}" fill="${color}"/>`;
      }).join('');
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
      <div id="verdict-mini-chart" class="verdict-mini-chart" role="button" tabindex="0" aria-label="Open Price Chart" title="Open Price Chart"
           onclick="openPriceChartModal()"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openPriceChartModal();}">
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
    const contributors = Array.isArray(dec.contributors) ? dec.contributors : [];
    const evidenceCoverage = Number.isFinite(Number(dec.evidenceCoverage)) ? Number(dec.evidenceCoverage) : 0;
    const decisionDegraded = dec.degraded === true;
    const decisionMissing = Array.isArray(dec.missingInputs) ? dec.missingInputs : [];
    const feedState = (window.AppState && AppState.feedState) || {};
    const partialData = feedState.quality === 'PARTIAL';
    const partialMissing = Array.isArray(feedState.missing) ? feedState.missing : [];
    const signalObservedAt = (sigs.find(s=>s.observedAt) || {}).observedAt || dec.decisionTimestamp || '';
    const signalObservedLabel = signalObservedAt
      ? new Date(signalObservedAt).toLocaleTimeString('en-IN', {hour:'2-digit', minute:'2-digit', second:'2-digit'})
      : 'time unavailable';
    const signalFreshness = String(feedState.status || 'UNKNOWN').toUpperCase();

    const biasIsBull = bias === 'BULLISH';
    const biasIsBear = bias === 'BEARISH';
    const biasCardCls = biasIsBull ? 'bullish' : biasIsBear ? 'bearish' : 'neutral';
    const confColor  = conf >= 65 ? 'var(--pos)' : conf >= 40 ? 'var(--warn)' : 'var(--neg)';

    const sevDot = s => s === 'warn' ? '\u26A0' : s === 'ok' ? '\u2713' : '\u00B7';
    const sevClr = s => s === 'warn' ? 'var(--neg)' : s === 'ok' ? 'var(--pos)' : 'var(--text-tertiary)';

    // Strike-level signals are observations, not 32 independent decision
    // votes. Collapse repeated CE/PE structures into directional families
    // so agreement is visible before the raw audit trail.
    const classifyActiveSignal = (signal) => {
      const text = String(signal && signal.text || '').toLowerCase();
      if(/\b(ce|call)\b.*\b(writing|short build)/.test(text)) return {key:'ce-writing', label:'CE writing', direction:'bearish'};
      if(/\b(pe|put)\b.*\bunwind/.test(text)) return {key:'pe-unwinding', label:'PE unwinding', direction:'bearish'};
      if(/\b(pe|put)\b.*\b(writing|short build)/.test(text)) return {key:'pe-writing', label:'PE writing', direction:'bullish'};
      if(/\b(ce|call)\b.*\bunwind/.test(text)) return {key:'ce-unwinding', label:'CE unwinding', direction:'bullish'};
      if(/\b(ce|call)\b.*\b(buying|long build)/.test(text)) return {key:'ce-buying', label:'CE buying', direction:'bullish'};
      if(/\b(pe|put)\b.*\b(buying|long build)/.test(text)) return {key:'pe-buying', label:'PE buying', direction:'bearish'};
      return {key:'other', label:'Other evidence', direction:'neutral'};
    };
    const signalFamilies = new Map();
    sigs.forEach(signal => {
      const family = classifyActiveSignal(signal);
      const current = signalFamilies.get(family.key) || {...family, count:0};
      current.count += 1;
      signalFamilies.set(family.key, current);
    });
    const directionalFamilies = [...signalFamilies.values()].filter(f => f.direction !== 'neutral');
    const bullishSignalCount = directionalFamilies.filter(f => f.direction === 'bullish').reduce((sum,f) => sum+f.count,0);
    const bearishSignalCount = directionalFamilies.filter(f => f.direction === 'bearish').reduce((sum,f) => sum+f.count,0);
    const directionalSignalCount = bullishSignalCount + bearishSignalCount;
    const dominantSignalDirection = bearishSignalCount > bullishSignalCount ? 'Bearish' : bullishSignalCount > bearishSignalCount ? 'Bullish' : 'Mixed';
    const dominantSignalCount = Math.max(bullishSignalCount, bearishSignalCount);
    const activeSignalAgreement = directionalSignalCount ? Math.round(dominantSignalCount / directionalSignalCount * 100) : 0;
    const activeSignalRead = directionalSignalCount === 0
      ? 'No directional CE/PE structure is available.'
      : dominantSignalDirection === 'Mixed'
        ? 'Bullish and bearish option structures are balanced; wait for separation.'
        : `${dominantSignalDirection} structure · ${activeSignalAgreement}% agreement across classified strike observations.`;

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

    // "Why" explains the weighted decision calculation; Active Signals
    // below remains the single owner of live alerts and confirmations.
    // Ranking by absolute weighted contribution surfaces the evidence that
    // moved the composite score most, irrespective of bullish/bearish sign.
    const heroContributors = contributors
      .filter(c => c.available !== false && Number.isFinite(Number(c.weightedContribution)))
      .sort((a,b) => Math.abs(Number(b.weightedContribution)) - Math.abs(Number(a.weightedContribution)))
      .slice(0,3);
    const heroWhyHtml = heroContributors.length
      ? heroContributors.map(c => {
          const contribution = Number(c.weightedContribution);
          const direction = contribution > 0 ? 'Bullish' : contribution < 0 ? 'Bearish' : 'Neutral';
          const color = contribution > 0 ? 'var(--pos)' : contribution < 0 ? 'var(--neg)' : 'var(--text-tertiary)';
          const marker = contribution > 0 ? '\u2191' : contribution < 0 ? '\u2193' : '\u00b7';
          const label = escapeHtml(c.label || c.key || 'Evidence');
          const title = escapeHtml(`${c.weight || 0}% weight · ${contribution >= 0 ? '+' : ''}${contribution.toFixed(3)} contribution`);
          return `<div class="decision-flow-line">
            <span class="decision-flow-dot" style="color:${color};">${marker}</span>
            <span title="${title}">${label} · ${direction}</span>
          </div>`;
        }).join('')
      : `<div class="decision-flow-line"><span class="decision-flow-dot">·</span><span>${explainVal(vrd.pcr) || explainVal(vrd.vix) || 'No strong confirming signal yet.'}</span></div>`;

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
        <div class="verdict-conf-label">Evidence Confidence</div>
        <div class="verdict-conf-big" style="color:${confColor};">${conf}%</div>
        <div class="verdict-conf-msg">Coverage ${evidenceCoverage}%</div>
        ${decisionDegraded ? `<div class="verdict-data-quality" title="Missing: ${decisionMissing.join(', ')}">DEGRADED${decisionMissing.length ? ' · '+decisionMissing.join(', ') : ''}</div>` : ''}
        ${partialData ? `<div class="verdict-data-quality" title="Missing: ${partialMissing.join(', ')}">PARTIAL DATA${partialMissing.length ? ' · '+partialMissing.join(', ') : ''}</div>` : ''}
      </div>
    </div>
    <div class="decision-flow" aria-label="Decision summary">
      <section class="decision-flow-block decision-flow-why">
        <div class="decision-flow-label">Why</div>
        <div class="decision-flow-content">${heroWhyHtml}</div>
        <div class="decision-flow-foot" title="${vrd.pcr || ''} · ${vrd.vix || ''}">PCR ${shortVal(vrd.pcr)} · VIX ${shortVal(vrd.vix)}</div>
      </section>
      <section class="decision-flow-block decision-flow-levels">
        <div class="decision-flow-label">Key Levels</div>
        <div class="decision-flow-level-grid">
          <div><span>ATM</span><strong>${atm?fmtI(atm):'—'}</strong></div>
          <div><span>Max Pain</span><strong>${d.maxPain!=null?fmtI(d.maxPain):'—'}</strong></div>
        </div>
        <div class="oic-tile decision-flow-walls">
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
      </section>
      <section class="decision-flow-block decision-flow-action">
        <div class="decision-flow-label">Action</div>
        <div class="decision-flow-action-text">${act && act !== '—' ? act : 'Wait for a clearer edge'}</div>
        <div class="decision-flow-action-meta">
          <span>Grade <strong style="color:${gradeColor};">${risk.tradeGrade || '—'}</strong></span>
          <span>IV <strong style="color:${ivRgColor};">${risk.ivRegime || '—'}</strong></span>
        </div>
      </section>
    </div>
  </div>

  <!-- ── DECISION DETAIL — Tier-3 collapsible ── -->
  <details class="card" id="decision-detail-card" style="margin-bottom:10px;" ${detailOpen ? 'open' : ''}>
    <summary>
      <div class="card-head"><span class="ic">🧭</span>Full Evidence &amp; Risk Levels</div>
      <span class="chev">▶</span>
    </summary>
    <div class="detail-body">

      <div class="dd-col" style="margin-bottom:10px;">
        <div class="dd-col-title">Decision Evidence · ${evidenceCoverage}% coverage</div>
        <div class="dd-sig-list">
          ${contributors.length ? contributors.map(c => {
            const available = c.available !== false;
            const contribution = available && c.weightedContribution != null
              ? `${Number(c.weightedContribution) >= 0 ? '+' : ''}${Number(c.weightedContribution).toFixed(3)}`
              : 'unavailable';
            return `<div class="dd-sig">
              <span style="color:${available?'var(--text-primary)':'var(--neg)'};font-weight:700;min-width:170px;">${escapeHtml(c.label || c.key || 'Signal')}</span>
              <span style="color:var(--text-tertiary);">${available ? `${c.weight || 0}% weight · ${contribution}` : 'Missing — excluded from score'}</span>
            </div>`;
          }).join('') : '<div class="dd-empty">Contributor evidence unavailable.</div>'}
          ${dec.decisionTimestamp ? `<div class="dd-sig"><span style="color:var(--text-tertiary);">State ${dec.stateVersion || '—'} · ${dec.decisionTimestamp}</span></div>` : ''}
        </div>
      </div>

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
          <div class="dd-col-title">Active Signals · ${sigs.length} observations · ${escapeHtml(signalFreshness)}</div>
          <div style="font-size:9px;color:var(--text-tertiary);margin:-2px 0 6px;">Observed ${escapeHtml(signalObservedLabel)}</div>
          ${sigs.length ? `
          <div class="signal-cluster-summary ${dominantSignalDirection.toLowerCase()}">
            <div><span>Dominant read</span><strong>${activeSignalRead}</strong></div>
            <div class="signal-direction-counts"><span class="bullish">Bullish ${bullishSignalCount}</span><span class="bearish">Bearish ${bearishSignalCount}</span></div>
          </div>
          <div class="signal-family-grid" aria-label="Grouped active signal families">
            ${[...signalFamilies.values()].sort((a,b)=>b.count-a.count).map(f=>`<div class="signal-family ${f.direction}"><span>${escapeHtml(f.label)}</span><strong>${f.count}</strong></div>`).join('')}
          </div>
          <details class="signal-observation-details">
            <summary>View ${sigs.length} strike-level observations</summary>
            <div class="dd-sig-list">
            ${sigs.map(s=>`
              <div class="dd-sig" data-signal-id="${escapeHtml(s.id || s.text)}">
                <span style="color:${sevClr(s.severity)};font-weight:700;flex-shrink:0;">${sevDot(s.severity)}</span>
                <span style="color:${s.severity==='warn'||s.severity==='ok'?'var(--text-primary)':'var(--text-tertiary)'};">${escapeHtml(s.text)}</span>
                <span style="margin-left:auto;font-size:8px;font-weight:800;color:${sevClr(s.severity)};">${s.severity==='warn'?'WARNING':s.severity==='ok'?'CONFIRM':'INFO'}</span>
              </div>`).join('')}
            </div>
          </details>` : '<div class="dd-empty">No active signals.</div>'}
        </div>

        <div class="dd-col">
          <div class="dd-col-title">S &amp; R Levels</div>
          ${hasTrapWarn ? `
          <div class="dd-trap">
            <span class="ic">\u26A0</span>
            <span class="lbl">Trap Warning</span>
            <span class="txt" title="${escapeHtml(risk.trapWarn)}">${escapeHtml(risk.trapWarn)}</span>
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
  // the canonical Option Chain surface. It deliberately uses only red for
  // CE and green for PE; strike links open the dashboard-native detail view.
ChainView.prototype.buildChainSummaryHtml = function(d) {
  const chain = getFilteredChain(d);
  const tableOpen = this.chainTableOpen === true;
  const greeksVisible = this.chainGreeksVisible === true;
  const greeksByStrike = new Map((d.greeks || []).map(g => [Number(g.strike), g]));
  const structureOi = {};
  chain.forEach(r => {
    structureOi[Number(r.strike)] = {
      ce: Number(r.ceOI) || 0,
      pe: Number(r.peOI) || 0,
      ceChg: Number(r.ceChgOI) || 0,
      peChg: Number(r.peChgOI) || 0,
    };
  });
  const structureByStrike = typeof marketStructureLabels === 'function'
    ? marketStructureLabels(chain, activeAtm(d), structureOi, Number(d.maxPain))
    : {};

  if(!chain.length){
    return `
  <div class="section-card sc-green" id="chain-summary-card">
    <button type="button" class="section-header nav-card-header" onclick="openOptionChainModal(this)"
      aria-expanded="${tableOpen}" aria-controls="option-chain-table">
      <span class="section-title nav-card-header-label"><span class="section-icon">📊</span>Option Chain Snapshot</span>
      <span class="nav-card-header-arrow" aria-hidden="true">${tableOpen?'▾':'↗'}</span>
    </button>
    <div class="dd-empty">Awaiting chain data…</div>
  </div>`;
  }

  // Unit-aware K/L/Cr formatting comes from shared/utils/formatters.js.
  const signedFmt = (v) => (v>0?'+':'') + fmtCrLK(v);
  // signColor()'s default neutral is already --text-primary, matching the
  // reference mockup's "0 stays bold/white, not greyed out" behavior.

  // ── Positioning summary ─────────────────────────────────────────
  // D-04 owns aggregate positioning only. Intraday OI/capital FLOW has
  // moved to D-07 so this card answers one question: where is positioning
  // concentrated across the currently visible strike range?
  const {totalCe, totalPe, pcr} = computeRangeChainTotals(chain);

  const totalCeChg = chain.reduce((s,r)=>s+(r.ceChgOI||0),0);
  const totalPeChg = chain.reduce((s,r)=>s+(r.peChgOI||0),0);
  const netOi = totalPe-totalCe;
  const netChgOi = totalPeChg-totalCeChg;
  const prevCe = totalCe-totalCeChg, prevPe = totalPe-totalPeChg;
  const prevPcr = prevPe/(prevCe||1);
  const pcrShift = pcr-prevPcr;

  const totalCeVol = chain.reduce((sum,r)=>sum+(r.ceVol||0),0);
  const totalPeVol = chain.reduce((sum,r)=>sum+(r.peVol||0),0);
  const ceVolOi = totalCeVol/(totalCe||1);
  const peVolOi = totalPeVol/(totalPe||1);

  const rngLabel = (() => { const rng = typeof _chainRange !== 'undefined' ? _chainRange : 10; return rng===9999?'ALL STRIKES':'±'+rng+' STRIKES'; })();
  const rngTag = (() => { const rng = typeof _chainRange !== 'undefined' ? _chainRange : 10; return rng===9999?'All':'±'+rng; })();
  const visibleStrikes = new Set(chain.map(r => Number(r.strike)));
  const netOiVelocityFor = (windowMin) => {
    const block = (d.oiVelocity || []).find(b => Number(b.window) === windowMin);
    if(!block || !Array.isArray(block.rows)) return null;
    let ce = 0, pe = 0, hasValue = false;
    block.rows.forEach((r) => {
      if(!visibleStrikes.has(Number(r.strike))) return;
      const ceV = Number(r.ceDOI), peV = Number(r.peDOI);
      if(Number.isFinite(ceV)){ ce += ceV; hasValue = true; }
      if(Number.isFinite(peV)){ pe += peV; hasValue = true; }
    });
    return hasValue ? pe - ce : null;
  };
  const oiChangePeriods = [5,15,30].map(windowMin => ({
    label:`${windowMin}m`, value:netOiVelocityFor(windowMin),
  }));
  const fmtNetOiVelocity = (v) => v==null || !Number.isFinite(v) ? '—' : `${v>0?'+':''}${fmtK(v)}`;

  return `
  <div class="section-card sc-green" id="chain-summary-card">
    <button type="button" class="section-header nav-card-header" onclick="openOptionChainModal(this)"
      aria-expanded="${tableOpen}" aria-controls="option-chain-table">
      <span class="oi-snap-heading nav-card-header-label">
        <svg width="20" height="16" viewBox="0 0 20 16" fill="none"><rect x="1" y="5" width="7" height="11" rx="1" fill="var(--neg)"/><rect x="12" y="1" width="7" height="15" rx="1" fill="var(--pos)"/></svg>
        Option Chain Snapshot
      </span>
      <span class="oi-snap-badge">${rngLabel}</span>
      <span class="nav-card-header-arrow" aria-hidden="true">${tableOpen?'▾':'↗'}</span>
    </button>

    <div class="oi-snap-content">

    <div class="oi-snap-grid">

      <div class="oi-snap-card pos">
        <div class="oi-snap-card-top">
          <div class="oi-snap-icon pos">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--pos)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>
          </div>
          <div class="oi-snap-title">OI Summary</div>
        </div>
        <div class="oi-snap-primary">
          <span><small>Net OI · PE−CE</small><strong style="color:${signColor(netOi)}">${signedFmt(netOi)}</strong></span>
          <span class="oi-snap-primary-pcr"><small>Range PCR · ${rngTag}</small><strong>${fmtN(pcr,2)}</strong></span>
        </div>
        <div class="oi-snap-sides" aria-label="Call and put open interest totals">
          <span class="ce"><small>CE OI</small><strong>${fmtCrLK(totalCe)}</strong></span>
          <span class="pe"><small>PE OI</small><strong>${fmtCrLK(totalPe)}</strong></span>
        </div>
        <div class="oi-snap-inline-metrics" aria-label="Volume to open interest ratios">
          <div class="oi-snap-inline-heading"><strong>Vol/OI</strong><small>Visible range</small></div>
          <div class="oi-snap-inline-grid two">
            <span><small>CE Vol/OI</small><strong class="ce">${fmtN(ceVolOi,2)}x</strong></span>
            <span><small>PE Vol/OI</small><strong class="pe">${fmtN(peVolOi,2)}x</strong></span>
          </div>
        </div>
      </div>

      <div class="oi-snap-card info">
        <div class="oi-snap-card-top">
          <div class="oi-snap-icon info">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--info)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 5"/><polyline points="15 5 21 5 21 11"/></svg>
          </div>
          <div class="oi-snap-title">Chg OI Summary</div>
          <button type="button" class="oi-snap-chart-link" onclick="event.stopPropagation();openOIDashboardModal()" aria-label="Open OI Flow chart">OI Flow chart ↗</button>
        </div>
        <div class="oi-snap-primary">
          <span><small>Full-day Net ΔOI · PE−CE</small><strong style="color:${signColor(netChgOi)}">${signedFmt(netChgOi)}</strong></span>
          <span class="oi-snap-primary-pcr"><small>Range PCR Δ</small><strong>${signedFmt(pcrShift)}</strong></span>
        </div>
        <div class="oi-snap-sides" aria-label="Call and put change in open interest totals">
          <span class="ce"><small>CE ΔOI</small><strong>${signedFmt(totalCeChg)}</strong></span>
          <span class="pe"><small>PE ΔOI</small><strong>${signedFmt(totalPeChg)}</strong></span>
        </div>
        <div class="oi-snap-inline-metrics" aria-label="Net OI flow and velocity">
          <div class="oi-snap-inline-heading"><strong>OI Flow / Velocity</strong><small>PE−CE ΔOI</small></div>
          <div class="oi-snap-inline-grid three">
            ${oiChangePeriods.map(({label,value}) => `<span><small>${label}</small><strong style="color:${value==null?'var(--text-tertiary)':signColor(value)}">${fmtNetOiVelocity(value)}</strong></span>`).join('')}
          </div>
        </div>
      </div>

    </div>

    <div class="oc-native-chain" id="option-chain-table" ${tableOpen?'':'hidden'}>
      <div class="oc-native-scroll">
        <table class="oc-ledger-table" aria-label="Option Chain ledger by strike">
          <colgroup>
            <col class="c-iv"><col class="c-vol"><col class="c-prem"><col class="c-ltp"><col class="c-ltp">
            <col class="c-strike"><col class="c-oi"><col class="c-chg"><col class="c-sig"><col class="c-foot"><col class="c-struct">
          </colgroup>
          <thead>
            <tr>
              <th>IV <small>PE / CE</small></th>
              <th>Volume <small>PE / CE</small></th>
              <th>Premium ₹ <small>PE / CE</small></th>
              <th class="ce">CE LTP <small>change</small></th>
              <th class="pe">PE LTP <small>change</small></th>
              <th class="strike">Strike <small>PCR</small></th>
              <th>Open Int <small>PE / CE</small></th>
              <th>Chg OI <small>PE / CE</small></th>
              <th>Signal <small>composite</small></th>
              <th>Footprint <small>0–100</small></th>
              <th>Structure <small>strike</small></th>
            </tr>
          </thead>
          <tbody>
            ${chain.map(r => { const sig=chainCombinedSignal(r.ceSignal,r.peSignal); const isAtm=r.strike===activeAtm(d); const g=greeksByStrike.get(Number(r.strike))||{}; const structure=structureByStrike[Number(r.strike)]; const structureText=structure?structure.text:(isAtm?'ATM':'—'); const structureStyle=structure&&structure.color?` style="color:${structure.color}"`:''; return `<tr class="oc-ledger-row ${isAtm?'atm':''}">
              <td><div class="oc-ledger-stack"><span class="pe">${fmtN(r.peIV,2)}%</span><span class="ce">${fmtN(r.ceIV,2)}%</span></div></td>
              <td><div class="oc-ledger-stack"><span class="pe">${fmtCrLK(r.peVol)}</span><span class="ce">${fmtCrLK(r.ceVol)}</span></div></td>
              <td><div class="oc-ledger-stack"><span class="pe">₹${fmtCrLK(r.pePremiumLocked)}</span><span class="ce">₹${fmtCrLK(r.cePremiumLocked)}</span></div></td>
              <td class="ltp ce"><button type="button" onclick="event.stopPropagation();ptOpenQuickOrder(event,${Number(r.strike)},'CE',${r.ceLTP==null?'null':Number(r.ceLTP)})" aria-label="Buy or sell ${fmtI(r.strike)} CE"><strong>${fmtN(r.ceLTP,2)}</strong><small class="${r.ceChg>=0?'up':'down'}">${r.ceChg==null?'—':signedFmt(r.ceChg)}</small></button></td>
              <td class="ltp pe"><button type="button" onclick="event.stopPropagation();ptOpenQuickOrder(event,${Number(r.strike)},'PE',${r.peLTP==null?'null':Number(r.peLTP)})" aria-label="Buy or sell ${fmtI(r.strike)} PE"><strong>${fmtN(r.peLTP,2)}</strong><small class="${r.peChg>=0?'up':'down'}">${r.peChg==null?'—':signedFmt(r.peChg)}</small></button></td>
              <td class="strike"><button type="button" onclick="event.stopPropagation();openOptionChainAtStrike(${Number(r.strike)})" aria-label="Open Strike Detail for ${fmtI(r.strike)}"><strong>${fmtI(r.strike)}</strong><small>PCR ${r.ceOI?fmtN((r.peOI||0)/r.ceOI,2):'—'}</small></button></td>
              <td><div class="oc-ledger-stack"><span class="pe">${fmtCrLK(r.peOI)}</span><span class="ce">${fmtCrLK(r.ceOI)}</span></div></td>
              <td><div class="oc-ledger-stack"><span class="pe ${r.peChgOI>=0?'up':'down'}">${signedFmt(r.peChgOI)}</span><span class="ce ${r.ceChgOI>=0?'up':'down'}">${signedFmt(r.ceChgOI)}</span></div></td>
              <td class="signal"><span class="oc-ledger-signal ${sig.cls||''}">${escapeHtml(sig.label||'Mixed')}</span></td>
              <td class="footprint"><strong>${r.footprintScore==null?'—':fmtN(r.footprintScore,0)}</strong></td>
              <td class="structure"${structureStyle} title="${escapeHtml(structureText)}">${escapeHtml(structureText)}</td>
            </tr><tr class="oc-ledger-greeks" ${greeksVisible?'':'hidden'}><td colspan="11">
              <div><b class="ce">CE</b> Δ ${fmtN(g.cDelta,3)} · Γ ${fmtN(g.cGamma,5)} · Θ ${fmtN(g.cTheta,2)} · Vega ${fmtN(g.cVega,2)}</div>
              <div><b class="pe">PE</b> Δ ${fmtN(g.pDelta,3)} · Γ ${fmtN(g.pGamma,5)} · Θ ${fmtN(g.pTheta,2)} · Vega ${fmtN(g.pVega,2)}</div>
            </td></tr>`; }).join('')}
          </tbody>
        </table>
      </div>
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
ChainView.prototype.buildVolOiDetailHtml = function(d) {
  const chain = getFilteredChain(d);
  if(!chain.length) return '';

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
      <div style="text-align:right;font-size:0.6875rem;font-weight:700;color:var(--neg);text-transform:uppercase;letter-spacing:.06em;">CE IV</div>
      <div style="text-align:center;font-size:0.6875rem;font-weight:700;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.06em;">Strike</div>
      <div style="text-align:left;font-size:0.6875rem;font-weight:700;color:var(--pos);text-transform:uppercase;letter-spacing:.06em;">PE IV</div>
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
        <span style="font-size:0.75rem;font-family:var(--mono);color:var(--neg);font-weight:600;white-space:nowrap;min-width:44px;text-align:right;">${fmtN(ceIV,2)}%</span>
        <div style="flex:1 1 auto;min-width:0;display:flex;justify-content:flex-end;">
          <div style="height:7px;border-radius:2px 0 0 2px;background:var(--neg);width:${ceWidthPct}%;min-width:4px;"></div>
        </div>
      </div>
      <div style="text-align:center;padding:0 4px;">
        <span style="font-family:var(--mono);font-size:0.75rem;font-weight:${ia?700:500};color:${ia?'var(--pos)':'var(--text-secondary)'};white-space:nowrap;">${fmtI(r.strike)}${ia?' ★':''}</span>
      </div>
      <div style="display:flex;align-items:center;justify-content:flex-start;gap:6px;">
        <div style="flex:1 1 auto;min-width:0;">
          <div style="height:7px;border-radius:0 2px 2px 0;background:var(--pos);width:${peWidthPct}%;min-width:4px;"></div>
        </div>
        <span style="font-size:0.75rem;font-family:var(--mono);color:var(--pos);font-weight:600;white-space:nowrap;min-width:44px;">${fmtN(peIV,2)}%</span>
      </div>
    </div>`;
  });
  const minIV = Math.min(...ivRows.map(r => Math.min(r.ceIV||0, r.peIV||0)));

  return `<div class="iv-surface-plot">${headerHtml}<div class="iv-surface-rows">${rowsHtml}</div></div>
    <div class="iv-surface-footer">
      <span>Skew <strong style="color:var(--warn);">${fmtN(d.atmSkew,2)}%</strong> at ATM</span>
      <span>Max IV <strong style="color:var(--neg);">${fmtN(maxIV,2)}%</strong></span>
      <span>Min IV <strong style="color:var(--pos);">${fmtN(minIV,2)}%</strong></span>
    </div>`;
};
