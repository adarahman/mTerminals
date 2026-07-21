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
  if (isBear === undefined) {
    isBear = (d.decision?.bias==='BEARISH')||(d.compositeBias||'').toLowerCase().includes('bear');
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
      <span id="topbar-spot" class="spot${isBear?' bearish':''}${spotFlashCls}">${fmtI(d.spot)}</span>
      ${d.spotChgPct!==undefined?`<span id="topbar-badge" class="badge ${d.spotChgPct>=0?'badge-bull':'badge-bear'}">${d.spotChgPct>=0?'▲':'▼'} ${Math.abs(d.spotChgPct).toFixed(2)}% (${d.spotChange>=0?'+':''}${Math.round(d.spotChange||0)})</span>`:''}
      <span id="topbar-chart-icon" class="chart-icon" title="Open price chart" onclick="window.open('PriceChart/price-chart.html?symbol=${encodeURIComponent(d.symbol||'NIFTY')}','_blank')">📈</span>
      ${renderIndexTicker(d)}
    </div>
    <div class="expiry-strip">
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
      ${this.renderFundPillHtml(d)}
    </div>
  </div>`;
};

  // Always-visible Profit/Fund readout so a square-off decision doesn't
  // require opening the (collapsed-by-default) Paper Trading panel first.
  // ptComputeFundSummary() lives in paper-trading.js, which loads after
  // this file in DashboardPro.html — safe to call here anyway since this
  // only ever runs at render time (a live WS tick), by which point every
  // script tag has already executed. Guarded regardless, in case
  // paper-trading.js is ever removed/reordered or the portfolio feed
  // hasn't arrived yet.
ChainView.prototype.renderFundPillHtml = function(d) {
    if (typeof window.ptComputeFundSummary !== 'function') return '';
    const fs = window.ptComputeFundSummary(d);
    if (!fs) return '';
    const pnlColor = fs.netPnl >= 0 ? 'var(--green)' : 'var(--red)';
    const warnCls = fs.lowFund ? ' pt-topbar-pill-warn' : '';
    const openPanel = "onclick=\"var p=document.getElementById('pt-panel'); if(p) p.classList.add('open');\"";
    const fundUnavailable = fs.fundSource === 'live-unavailable';
    return `<div class="expiry-divider"></div>
      <div class="expiry-pill pt-topbar-pill${warnCls}" ${openPanel} title="Net P&amp;L${fs.fundSource==='live-real'?' (real, from AngelOne)':fs.isLive?' after charges (paper model — live mode is on)':' after charges'} — click for full Paper Trading detail">
        <span class="expiry-pill-label">P&amp;L${fs.fundSource==='live-unavailable'?' (paper)':''}</span>
        <span class="expiry-pill-val" style="color:${pnlColor}">${fs.netPnl>=0?'+':''}${fmtI(fs.netPnl)}</span>
      </div>
      <div class="expiry-pill pt-topbar-pill${warnCls}" ${openPanel} title="${fundUnavailable?'Live account funds aren\'t wired up yet — see ptComputeFundSummary() in paper-trading.js':fs.fundSource==='live-real'?'Real available margin, from AngelOne rmsLimit()':'Available margin (approx.)'} — click for full Paper Trading detail">
        <span class="expiry-pill-label">Fund</span>
        <span class="expiry-pill-val" style="color:${fundUnavailable?'var(--txt3)':(fs.lowFund?'var(--red)':'var(--txt)')}">${fundUnavailable?'n/a':fmtI(fs.fund)}</span>
      </div>`;
};

ChainView.prototype.renderDecisionBoxHtml = function(d) {
    const dec  = d.decision || {};
    const vrd  = dec.verdicts || {};
    const sigs = dec.activeSignals || [];
    const auto = dec.autoStrategy || {};
    const bias = dec.bias || d.compositeBias || '—';
    const str  = dec.biasStrength || '';
    const conf = dec.confidence || 0;
    const act  = dec.action || '—';
    const actType = dec.actionType || '';
    const conflict = dec.conflictFlag || false;

    const biasIsBull = bias === 'BULLISH';
    const biasIsBear = bias === 'BEARISH';
    const biasColor  = biasIsBull ? 'var(--green)' : biasIsBear ? 'var(--red)' : 'var(--amber)';
    const confColor  = conf >= 65 ? 'var(--green)' : conf >= 40 ? 'var(--amber)' : 'var(--red)';

    const sevDot = s => s === 'warn' ? '\u26A0' : s === 'ok' ? '\u2713' : '\u00B7';
    const sevClr = s => s === 'warn' ? 'var(--red)' : s === 'ok' ? 'var(--green)' : 'var(--txt3)';

    // Verdict rows — unique to this panel only (IV Rank shown in ATM Greeks; DTE in top-bar)
    const verdictDefs = [
      { k: 'PCR',      v: vrd.pcr     || '—' },
      { k: 'VIX',      v: vrd.vix     || '—' },
      { k: 'Max Pain', v: vrd.maxPain || '—' },
      { k: 'CE Wall',  v: vrd.ceWall  || '—' },
      { k: 'PE Wall',  v: vrd.peWall  || '—' },
      { k: 'ATM IV',   v: vrd.atmIV ? vrd.atmIV + (vrd.ivRank ? ' · ' + vrd.ivRank.split('—')[0].trim() : '') : '—' },
    ].filter(x => x.v && x.v !== '—');

    // Auto strategy legs — trades routed through the exact same
    // ptExecuteLeg() the Strategy Payoff panel already uses (see
    // execBtn there), so a leg fired from here goes through identical
    // validation/dispatch/toast/portfolio-refresh behavior. autoStrategy
    // doesn't carry its own expiry field today, so resolve the same way
    // ptExecuteStrategy() does: per-leg expiry if present, else the
    // decision box's own expiry, run through ptResolveStrategyExpiry()
    // in case it's a NEAR/FAR label rather than a real date.
    const decSymbol = d.symbol || '';
    const legRows = (auto.legs || []).map(l => {
      const isBuy = l.action === 'BUY';
      const ac    = isBuy ? 'var(--green)' : 'var(--red)';
      const tc    = (l.type||'').toUpperCase() === 'CE' ? 'var(--red)' : 'var(--green)';
      const legLtp = parseFloat(l.ltp) || 0;
      const legExpiryReal = ptResolveStrategyExpiry(l.expiry || auto.expiry || d.expiry || '');
      const execBtn = legLtp > 0 ? `<span onclick="ptExecuteLeg('${decSymbol}','${legExpiryReal}',${l.strike||0},'${(l.type||'').toUpperCase()}','${l.action}',${l.lots||1},${legLtp})"
        title="Execute this leg as a paper order (expiry ${legExpiryReal||'—'})"
        style="cursor:pointer;font-size:9px;font-weight:800;padding:1px 5px;border-radius:4px;
        background:${ac};color:#0b0d12;margin-left:2px;">▶</span>` : '';
      return `<span class="leg-pill ${isBuy?'buy':'sell'}">
        <span style="color:${ac};font-weight:800;">${l.action}</span>
        <span style="color:${tc}">${(l.type||'').toUpperCase()}</span>
        <span style="color:var(--txt)">${fmtI(l.strike||0)}</span>
        ${legLtp>0?`<span style="color:${ac}">₹${fmtN(legLtp,1)}</span>`:''}
        ${execBtn}
      </span>`;
    }).join('');
    // "Execute all legs" now calls a dedicated function that re-reads
    // _data.decision.autoStrategy fresh at click time (see
    // ptExecuteDecisionStrategy() below) instead of baking a long
    // semicolon-chain of ptExecuteLeg(...) calls into one onclick — a
    // chain like that aborts entirely the moment any one call throws
    // (see the BUGFIX note on ptExecuteLeg), which is exactly what was
    // producing "only one leg executes." A plain function call has no
    // such failure mode and is easier to invoke/debug from the console.
    const decHasExecutableLeg = (auto.legs || []).some(l => parseFloat(l.ltp) > 0);

    return `
<div id="sec-decision" style="background:var(--bg1);border:1px solid var(--border);border-left:4px solid ${biasColor};border-radius:var(--radius);margin-bottom:10px;overflow:hidden;">

  <!-- ── HEADER ROW ── -->
  <div style="display:grid;grid-template-columns:auto 1fr auto auto;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--border);flex-wrap:wrap;">
    <div style="display:flex;flex-direction:column;gap:4px;">
      <span style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.09em;">Decision Engine</span>
      <span style="font-size:18px;font-weight:700;padding:4px 14px;border-radius:20px;background:${biasIsBull?'rgba(18,184,134,0.15)':biasIsBear?'rgba(250,82,82,0.15)':'rgba(245,159,0,0.15)'};color:${biasColor};border:1.5px solid ${biasColor};">
        ${bias}${str?' · '+str:''}${conflict?' ⚡':''}
      </span>
      ${d.futSignal && d.futSignal !== bias ? `<span style="font-size:10px;color:var(--txt3);">Fut: <strong style="color:${biasCls(d.futSignal).includes('bull')?'var(--green)':biasCls(d.futSignal).includes('bear')?'var(--red)':'var(--amber)'}">${d.futSignal}</strong></span>` : ''}
    </div>
    <div style="font-size:11px;color:var(--txt2);padding:0 4px;">${act}</div>
    
    <!-- S & R Levels -->
    <div style="min-width:140px;margin-right:8px;border-right:1px solid var(--border);padding-right:12px;">
      <div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;margin-bottom:4px;">S & R Levels</div>
      ${(()=>{
        const spot = d.spot || 0;
        const r1   = d.ceWall || 0;
        const s1   = d.peWall || 0;
        const step = d.strikeStep || 200;
        const r2   = r1 + step;
        const s2   = s1 - step;
        const dR1 = Math.abs(r1 - spot);
        const dR2 = Math.abs(r2 - spot);
        const dS1 = Math.abs(s1 - spot);
        const dS2 = Math.abs(s2 - spot);
        const maxDist = Math.max(dR1, dR2, dS1, dS2, 1);
        const proxBar = dist => {
          if (!dist || dist === 0) return 50.0;
          return Math.max(6, Math.round((1 - dist / maxDist) * 92));
        };
        const srCell = (lbl, val, d, clr) => {
          const w = proxBar(d);
          return `
            <div style="display:grid;grid-template-columns:16px 1fr 40px;align-items:center;gap:4px;">
              <span style="font-size:10px;font-weight:700;color:${clr};">${lbl}</span>
              <div style="height:6px;background:var(--bg2);border-radius:3px;overflow:hidden;">
                <div style="height:100%;width:${w}%;background:${clr};border-radius:3px;"></div>
              </div>
              <span style="font-size:10px;font-weight:700;font-family:var(--mono);color:${clr};text-align:right;">${fmtI(val)}</span>
            </div>`;
        };
        return `<div style="display:grid;grid-template-columns:1fr 1fr;row-gap:8px;column-gap:4px;">
          ${srCell('R1', r1, dR1, 'var(--red)')}
          ${srCell('S1', s1, dS1, 'var(--green)')}
          ${srCell('R2', r2, dR2, 'var(--red)')}
          ${srCell('S2', s2, dS2, 'var(--green)')}
        </div>`;
      })()}
    </div>
    
    <div style="text-align:right;">
      <div style="font-size:28px;font-weight:700;font-family:var(--mono);color:${confColor};">${conf}<span style="font-size:13px;">%</span></div>
      <div style="font-size:9px;color:var(--txt3);text-transform:uppercase;letter-spacing:.07em;">Confidence</div>
    </div>
  </div>

  <!-- ── BODY: 3 columns ── -->
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;">

    <!-- Col 1: Active Signals -->
    <div style="padding:12px 14px;border-right:1px solid var(--border);">
      <div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Active Signals</div>
      ${sigs.length ? sigs.map(s=>`
        <div style="display:flex;align-items:flex-start;gap:5px;padding:3px 0;border-bottom:1px solid var(--border);font-size:10px;line-height:1.4;">
          <span style="color:${sevClr(s.severity)};font-weight:700;flex-shrink:0;">${sevDot(s.severity)}</span>
          <span style="color:${s.severity==='warn'?'var(--txt)':s.severity==='ok'?'var(--txt)':'var(--txt3)'};">${s.text}</span>
        </div>`).join('') : '<div style="font-size:11px;color:var(--txt3);padding:4px 0;">No active signals.</div>'}
    </div>

    <!-- Col 2: Verdicts -->
    <div style="padding:12px 14px;border-right:1px solid var(--border);">
      <div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Verdicts</div>
      ${verdictDefs.map(r=>`
        <div style="padding:3px 0;border-bottom:1px solid var(--border);">
          <div style="font-size:9px;color:var(--txt3);font-weight:600;text-transform:uppercase;letter-spacing:.05em;">${r.k}</div>
          <div style="font-size:10px;color:var(--txt2);line-height:1.4;">${r.v}</div>
        </div>`).join('')}
    </div>

    <!-- Col 3: Strategy legs -->
    <div style="padding:12px 14px;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px;">
        <div style="font-size:9px;font-weight:700;color:var(--txt3);text-transform:uppercase;letter-spacing:.08em;">Strategy${auto.name?' — '+auto.name:''}</div>
        ${auto.name ? `<div style="display:flex;align-items:center;gap:6px;">
          <span style="font-size:9px;color:var(--txt3);">Suggested</span>
          ${auto.netPremium!=null?`<span style="font-size:10px;color:${auto.netPremium>=0?'var(--green)':'var(--red)'};font-weight:600;">${auto.netPremium>=0?'Credit':'Debit'} ₹${Math.abs(auto.netPremium).toFixed(1)}</span>`:''}
          ${decHasExecutableLeg ? `<span onclick="ptExecuteDecisionStrategy()" title="Place all legs of this strategy as paper orders"
            style="cursor:pointer;font-size:9px;font-weight:800;padding:2px 8px;border-radius:5px;
            background:var(--accent,#3b82f6);color:#fff;">▶ Execute</span>` : ''}
        </div>` : ''}
      </div>
      ${legRows ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;">${legRows}</div>` : '<div style="font-size:11px;color:var(--txt3);">No strategy computed.</div>'}
      ${auto.maxProfit!=null||auto.maxLoss!=null ? `<div style="display:flex;gap:10px;font-size:10px;flex-wrap:wrap;padding-top:6px;border-top:1px solid var(--border);">
        ${auto.maxProfit!=null?`<span style="color:var(--green);">Max Profit ₹${fmtI(auto.maxProfit)}</span>`:''}
        ${auto.maxLoss!=null?`<span style="color:var(--red);">Max Loss ₹${fmtI(auto.maxLoss)}</span>`:''}
        ${auto.ivRank!=null?`<span style="color:var(--txt3);">IV Rank ${auto.ivRank}</span>`:''}
      </div>` : ''}
    </div>

  </div>
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
  <div class="section-card" id="chain-summary-card">
    <div class="section-header"><span class="section-title">📊 Option Chain Snapshot</span></div>
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
  const netClr = (v) => v>0?'var(--green)':v<0?'var(--red)':'var(--txt3)';

  // ── OI summary ──
  const totalCe = chain.reduce((s,r)=>s+(r.ceOI||0),0);
  const totalPe = chain.reduce((s,r)=>s+(r.peOI||0),0);
  const oiTotal = totalCe+totalPe || 1;
  const pcr = totalPe/(totalCe||1);

  // ── Chg OI summary (+ how much that shifted PCR) ──
  const totalCeChg = chain.reduce((s,r)=>s+(r.ceChgOI||0),0);
  const totalPeChg = chain.reduce((s,r)=>s+(r.peChgOI||0),0);
  const chgTotal = Math.abs(totalCeChg)+Math.abs(totalPeChg) || 1;
  const prevCe = totalCe-totalCeChg, prevPe = totalPe-totalPeChg;
  const prevPcr = prevPe/(prevCe||1);
  const pcrShift = pcr-prevPcr;

  const netOi = totalPe-totalCe;
  const netChgOi = totalPeChg-totalCeChg;

  // ── dOI across 5/15/30m — net PE vs CE change per window, summed over
  // the currently visible strikes ──
  const VEL_WINDOWS = [5,15,30];
  const doiCols = VEL_WINDOWS.map(w=>{
    const block = (d.oiVelocity||[]).find(b=>b.window===w);
    const byStrike = {};
    if(block&&block.rows) block.rows.forEach(vr=>{byStrike[vr.strike]=vr;});
    const ceSum = chain.reduce((s,r)=>s+((byStrike[r.strike]||{}).ceDOI||0),0);
    const peSum = chain.reduce((s,r)=>s+((byStrike[r.strike]||{}).peDOI||0),0);
    return {w, ceSum, peSum, net: peSum-ceSum};
  });

  // ── Volume / OI ratio ──
  const totalCeVol = chain.reduce((s,r)=>s+(r.ceVol||0),0);
  const totalPeVol = chain.reduce((s,r)=>s+(r.peVol||0),0);
  const ratioCap = 3;
  const ceRatio = totalCeVol/(totalCe||1);
  const peRatio = totalPeVol/(totalPe||1);

  return `
  <div class="section-card" id="chain-summary-card">
    <div class="section-header">
      <span class="section-title">📊 Option Chain Snapshot</span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="window.open('OptionChain/option-chain.html','_blank')">Full Chain →</button>
    </div>
    <div style="display:grid;grid-template-columns:1.15fr 1.15fr 1fr 1fr;gap:16px;padding:10px 2px 4px;">

      <div>
        <div style="font-size:10px;color:var(--txt3);margin-bottom:8px;letter-spacing:.04em;">OI SUMMARY</div>
        <div style="height:6px;border-radius:999px;overflow:hidden;display:flex;background:var(--bg2);margin-bottom:8px;">
          <div style="width:${(totalPe/oiTotal)*100}%;background:linear-gradient(90deg,var(--green),transparent);"></div>
          <div style="width:${(totalCe/oiTotal)*100}%;background:linear-gradient(90deg,transparent,var(--red));"></div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:13px;font-weight:700;flex-wrap:wrap;">
          <span style="color:var(--green);">${fmtCrLK(totalPe)}</span>
          <span style="font-size:9px;color:var(--txt3);font-weight:400;">PE</span>
          <span style="background:rgba(245,166,35,.15);color:var(--amber);padding:2px 8px;border-radius:999px;font-size:11px;">PCR ${fmtN(pcr,2)}</span>
          <span style="font-size:9px;color:var(--txt3);font-weight:400;">CE</span>
          <span style="color:var(--red);">${fmtCrLK(totalCe)}</span>
        </div>
        <div style="font-size:11px;color:${netClr(netOi)};margin-top:6px;font-family:var(--mono);">Net (PE−CE) <b>${signedFmt(netOi)}</b></div>
      </div>

      <div>
        <div style="font-size:10px;color:var(--txt3);margin-bottom:8px;letter-spacing:.04em;">CHG OI SUMMARY</div>
        <div style="height:6px;border-radius:999px;overflow:hidden;display:flex;background:var(--bg2);margin-bottom:8px;">
          <div style="width:${(Math.abs(totalPeChg)/chgTotal)*100}%;background:linear-gradient(90deg,var(--green),transparent);"></div>
          <div style="width:${(Math.abs(totalCeChg)/chgTotal)*100}%;background:linear-gradient(90deg,transparent,var(--red));"></div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:13px;font-weight:700;flex-wrap:wrap;">
          <span style="color:var(--green);">${signedFmt(totalPeChg)}</span>
          <span style="font-size:9px;color:var(--txt3);font-weight:400;">PE</span>
          <span style="background:rgba(245,166,35,.15);color:var(--amber);padding:2px 8px;border-radius:999px;font-size:11px;">PCR Δ ${signedFmt(pcrShift)}</span>
          <span style="font-size:9px;color:var(--txt3);font-weight:400;">CE</span>
          <span style="color:var(--red);">${signedFmt(totalCeChg)}</span>
        </div>
        <div style="font-size:11px;color:${netClr(netChgOi)};margin-top:6px;font-family:var(--mono);">Net (PE−CE) <b>${signedFmt(netChgOi)}</b></div>
      </div>

      <div>
        <div style="font-size:10px;color:var(--txt3);margin-bottom:8px;letter-spacing:.04em;">dOI <span style="font-weight:400;">5 · 15 · 30m</span></div>
        <div style="display:flex;justify-content:space-between;gap:6px;">
          ${doiCols.map(c=>`
          <div style="text-align:center;flex:1;">
            <div style="font-size:10px;font-family:var(--mono);color:var(--green);">${signedFmt(c.peSum)}</div>
            <div style="font-size:10px;font-family:var(--mono);color:var(--red);margin-bottom:2px;">${signedFmt(c.ceSum)}</div>
            <div style="font-size:9px;color:var(--txt3);">${c.w}m</div>
            <div style="font-size:9px;font-weight:700;padding:1px 6px;border-radius:999px;margin-top:2px;display:inline-block;color:${netClr(c.net)};background:var(--bg2);">net ${signedFmt(c.net)}</div>
          </div>`).join('')}
        </div>
      </div>

      <div>
        <div style="font-size:10px;color:var(--txt3);margin-bottom:8px;letter-spacing:.04em;">VOLUME <span style="font-weight:400;">&amp; Vol/OI</span></div>
        <div style="display:flex;align-items:center;gap:6px;font-size:11px;margin-bottom:6px;">
          <span style="color:var(--red);min-width:18px;">CE</span>
          <span style="color:var(--red);font-family:var(--mono);min-width:56px;">${fmtCrLK(totalCeVol)}</span>
          <div style="flex:1;height:5px;border-radius:999px;background:var(--bg2);overflow:hidden;"><div style="height:100%;width:${Math.min(100,(ceRatio/ratioCap)*100)}%;background:var(--red);"></div></div>
          <span style="font-family:var(--mono);color:var(--txt3);font-size:10px;">${fmtN(ceRatio,2)}x</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;font-size:11px;">
          <span style="color:var(--green);min-width:18px;">PE</span>
          <span style="color:var(--green);font-family:var(--mono);min-width:56px;">${fmtCrLK(totalPeVol)}</span>
          <div style="flex:1;height:5px;border-radius:999px;background:var(--bg2);overflow:hidden;"><div style="height:100%;width:${Math.min(100,(peRatio/ratioCap)*100)}%;background:var(--green);"></div></div>
          <span style="font-family:var(--mono);color:var(--txt3);font-size:10px;">${fmtN(peRatio,2)}x</span>
        </div>
      </div>

    </div>
  </div>`;
};

  // ── IV ALERTS (main-dashboard summary card) ──
  // Same treatment as buildGreeksAlertsHtml: the full per-strike IV
  // surface (CE/PE bars, ATM ± 3) moved into its own modal
  // (openIvSurfaceModal()), so the main view only surfaces two things
  // worth reacting to — elevated put/call skew, and an IV rank sitting
  // near either extreme (options unusually rich or unusually cheap).
  // Thresholds are tunable heuristics, not backend-supplied flags.
ChainView.prototype.buildIvAlertsHtml = function(d, chain, atm) {
  const IV_ALERT_SKEW_PCT = 1.5;   // |atmSkew| above this is called "elevated"
  const IV_ALERT_RANK_HIGH = 80;   // ivRank above this is called "rich"
  const IV_ALERT_RANK_LOW  = 20;   // ivRank below this is called "cheap"
  const skew = d.atmSkew||0;
  const rank = d.ivRank||0;

  const alerts=[];
  if(Math.abs(skew) > IV_ALERT_SKEW_PCT){
    alerts.push({
      icon:'📐', clr:'var(--amber)',
      text:`Elevated ${skew>0?'put':'call'} skew — <strong>${fmtN(skew,2)}%</strong> at ATM`
    });
  }
  if(rank >= IV_ALERT_RANK_HIGH){
    alerts.push({
      icon:'🔺', clr:'var(--red)',
      text:`IV rank <strong>${Math.round(rank)}/100</strong> — options historically rich, consider selling premium`
    });
  } else if(rank <= IV_ALERT_RANK_LOW){
    alerts.push({
      icon:'🔻', clr:'var(--green)',
      text:`IV rank <strong>${Math.round(rank)}/100</strong> — options historically cheap, consider buying premium`
    });
  }

  const rows = alerts.length
    ? alerts.map(a=>`<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;background:rgba(255,255,255,0.03);border-left:2px solid ${a.clr};border-radius:4px;font-size:12px;line-height:1.4;"><span style="flex-shrink:0;">${a.icon}</span><span style="color:var(--txt2);">${a.text}</span></div>`).join('')
    : `<div style="font-size:12px;color:var(--txt3);padding:6px 8px;">No IV alerts right now — skew and rank both in normal range.</div>`;

  return `<div class="section-card" id="iv-alerts-card">
    <div class="section-header">
      <span class="section-title">IV Surface</span>
      <button class="sec-btn" style="padding:4px 10px;font-size:11px;" onclick="openIvSurfaceModal()" title="Open full IV surface">Full Surface →</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:6px;padding:2px 0;">
      ${rows}
    </div>
  </div>`;
};

  // ── FULL IV SURFACE (modal content) ──
  // Same per-strike CE/PE bar table + Skew/Max IV/Min IV footer that used
  // to render inline in the main template. Pulled out into its own method
  // so it can be (a) written once into the modal's static content div and
  // (b) refreshed from that same place on every tick / expiry switch via
  // renderIvSurfaceModal() below, instead of duplicating this markup in
  // both the initial template and the incremental-refresh path.
ChainView.prototype.buildIvSurfaceHtml = function(d, chain, atm) {
  const atmIdx = chain.findIndex(r => r.atm || r.strike === atm);
  let ivRows = [];
  if (atmIdx >= 0) {
    const start = Math.max(0, atmIdx - 3);
    const end = Math.min(chain.length, atmIdx + 4);
    ivRows = chain.slice(start, end);
  } else {
    ivRows = chain.slice(0, 6);
  }
  const maxIV = Math.max(...ivRows.map(r => Math.max(r.ceIV||0, r.peIV||0)), 1);
  const barMaxWidth = 160;

  let rowsHtml = '';
  ivRows.forEach(r => {
    const ia = r.atm || r.strike === atm;
    const ceIV = r.ceIV || 0;
    const peIV = r.peIV || 0;
    const ceWidth = Math.max((ceIV / maxIV) * barMaxWidth, 4);
    const peWidth = Math.max((peIV / maxIV) * barMaxWidth, 4);
    rowsHtml += `<div style="display:grid;grid-template-columns:1fr 80px 1fr;align-items:center;gap:0;padding:3px 6px;${ia?'background:rgba(18,184,134,0.08);border-radius:4px;':''}">
      <div style="display:flex;align-items:center;justify-content:flex-end;gap:5px;">
        <span style="font-size:9px;font-family:var(--mono);color:var(--red);font-weight:600;white-space:nowrap;">${fmtN(ceIV,2)}%</span>
        <div style="height:8px;border-radius:3px 0 0 3px;background:var(--red);width:${ceWidth}px;min-width:3px;flex-shrink:0;"></div>
      </div>
      <div style="text-align:center;padding:0 4px;">
        <span style="font-family:var(--mono);font-size:10px;font-weight:${ia?700:400};color:${ia?'var(--green)':'var(--txt3)'};">${fmtI(r.strike)}${ia?' ★':''}</span>
      </div>
      <div style="display:flex;align-items:center;justify-content:flex-start;gap:5px;">
        <div style="height:8px;border-radius:0 3px 3px 0;background:var(--green);width:${peWidth}px;min-width:3px;flex-shrink:0;"></div>
        <span style="font-size:9px;font-family:var(--mono);color:var(--green);font-weight:600;white-space:nowrap;">${fmtN(peIV,2)}%</span>
      </div>
    </div>`;
  });
  const minIV = Math.min(...ivRows.map(r => Math.min(r.ceIV||0, r.peIV||0)));

  return `<div style="display:flex;flex-direction:column;gap:4px;">${rowsHtml}</div>
    <div style="font-size:11px;color:var(--txt3);margin-top:10px;padding-top:10px;border-top:1px solid var(--border);display:flex;gap:20px;flex-wrap:wrap;">
      <span>Skew <strong style="color:var(--amber);">${fmtN(d.atmSkew,2)}%</strong> at ATM</span>
      <span>Max IV <strong style="color:var(--red);">${fmtN(maxIV,2)}%</strong></span>
      <span>Min IV <strong style="color:var(--green);">${fmtN(minIV,2)}%</strong></span>
    </div>`;
};
