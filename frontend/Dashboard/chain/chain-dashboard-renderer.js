// Dashboard-level chain rendering and incremental Decision Engine updates.

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
      badgeEl.textContent = `${d.spotChgPct>=0?'▲':'▼'} ${Math.abs(d.spotChgPct).toFixed(2)}%`;
      badgeEl.title = `${d.spotChange>=0?'+':''}${Math.round(d.spotChange||0)} points`;
    }
    const futureEl = document.getElementById('topbar-future');
    if(futureEl) futureEl.textContent = Number(d.future)>0?fmtI(d.future):'—';
    const basisEl = document.getElementById('topbar-basis');
    if(basisEl) basisEl.textContent = `Basis ${Number(d.basis)>=0?'+':''}${fmtN(Number(d.basis)||0,1)}`;
    const futuresExpiryEl = document.getElementById('futuresExpirySelect');
    if(futuresExpiryEl && d.futuresExpiry) futuresExpiryEl.value = d.futuresExpiry;
    const tickerEl = document.getElementById('index-ticker-bar');
    if (tickerEl && window.patchIndexTicker) patchIndexTicker(d);
    const dteEl = document.getElementById('dte-display');
    if (dteEl) dteEl.textContent = '· ' + (d.dte||0) + 'd';
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
  const chartWorkspace = document.getElementById('price-chart-modal');
  if(chartWorkspace && chartWorkspace.classList.contains('open') && app.modal){
    app.modal.renderPriceChartContext(d);
  }
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

ChainView.prototype.buildGreeksMoneynessHtml = function(d) {
  return `
    <div id="sec-greeks-moneyness" class="section-card sc-violet greeks-moneyness-card">
      <div class="section-header"><span class="section-title"><span class="section-icon">Δ</span>Greeks by Moneyness</span></div>
      <div class="greeks-moneyness-legend">
        <span><i style="background:#2a78d6;"></i>Delta (call)</span>
        <span><i style="background:#1baf7a;"></i>Gamma</span>
        <span><i style="background:#e34948;"></i>|Theta| decay</span>
        <span><i style="background:#eda100;"></i>Vega</span>
      </div>
      <div class="chart-expand-wrap greeks-moneyness-chart" role="button" tabindex="0" aria-label="Expand Greeks by Moneyness chart" onclick="openGreeksChartModal()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openGreeksChartModal();}" title="Click to expand">
        <span class="chart-expand-icon" title="Expand">⤢</span>
        <canvas id="greeksChart" role="img" aria-label="Line chart showing how delta, gamma, theta, and vega change shape from deep OTM through ATM to deep ITM for a call option, updated live from the option chain.">Delta rises steadily from OTM to ITM. Gamma, theta decay, and vega all peak at the at-the-money strike and fall off toward both deep ITM and deep OTM.</canvas>
      </div>
      <div class="atm-greeks-summary" aria-label="ATM Greeks at ${fmtI(d.atm)}">
        <div class="atm-greeks-heading" id="atm-greeks-heading">ATM ${fmtI(d.atm)}</div>
        <div><span>Delta</span><strong id="atm-greek-delta">${fmtN(d.atmDelta,4)}</strong></div>
        <div title="Gamma scaled ×10,000 for readability"><span>Gamma ×10⁴</span><strong id="atm-greek-gamma">${fmtN(d.atmGamma,4)}</strong></div>
        <div><span>Theta/day</span><strong id="atm-greek-theta" class="bear">${fmtN(d.atmTheta,2)}</strong></div>
        <div><span>Vega</span><strong id="atm-greek-vega">${fmtN(d.atmVega,2)}</strong></div>
      </div>
    </div>`;
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

  // The decision stays visible as the dashboard's stable anchor. Everything
  // below it is grouped into one workspace at a time so analysis no longer
  // becomes one very long stack of equally-weighted cards.
  const activeWorkspace = app.ui.dashboardWorkspace || 'positioning';
  const workspaceTab = (id, icon, label) => `<button type="button" class="dashboard-workspace-tab${activeWorkspace===id?' active':''}" data-workspace-tab="${id}" role="tab" aria-controls="workspace-${id}" aria-selected="${activeWorkspace===id?'true':'false'}" tabindex="${activeWorkspace===id?'0':'-1'}" onclick="switchDashboardWorkspace('${id}',this)"><span aria-hidden="true">${icon}</span>${label}</button>`;
  h += `<nav class="dashboard-workspace-tabs" role="tablist" aria-label="Dashboard analysis workspace">
    ${workspaceTab('positioning','⌗','Positioning')}
    ${workspaceTab('flow','₹','Flow')}
    ${workspaceTab('institutional','🏦','Institutional')}
    ${workspaceTab('validation','✓','Strategy &amp; Risk')}
  </nav>`;

  h += `<section id="workspace-positioning" class="dashboard-workspace" data-dashboard-workspace="positioning"${activeWorkspace==='positioning'?'':' hidden'}>`;

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
  h += '<div id="zone-structure" class="zone-divider zone-divider--primary"><span class="zone-divider-title">Positioning Evidence</span><span class="zone-divider-subtitle">Price, OI and dealer exposure</span></div>';

  // Built here and mounted later beside Institutional Activity Crux.
  const greeksMoneynessHtml = this.buildGreeksMoneynessHtml(d);

  // ── LARGE EXECUTIVE BOXES (original 3-column positioning grid) ──
  // Keep the exact markup so the live-refresh path can later compare it
  // without immediately rebuilding this entire section on its first tick.
  const executiveDashboardHtml = renderExecutiveDashboard(d);
  h += executiveDashboardHtml;

  // The chain summary is the gateway to the primary trading workspace, so
  // it owns a full row instead of competing for a third of the context grid.
  h += app.chain.buildChainSummaryHtml(d);

  // OI Flow is the time dimension of the snapshot above, not a separate
  // analytical destination. Keep its period changes and capital reading
  // immediately beside positioning.
  const velBlock=(d.oiVelocity||[]).find(b=>b.window===_velWin)||(d.oiVelocity||[])[0];
  const velByStrike={};
  if(velBlock&&velBlock.rows)velBlock.rows.forEach(vr=>{velByStrike[vr.strike]=vr;});
  const velMax=Math.max(...chain.map(r=>{const vr=velByStrike[r.strike]||{};return Math.max(Math.abs(vr.ceDOI||0),Math.abs(vr.peDOI||0));}),1);
  h += buildOiFlowSummaryHtml(chain, atm, velByStrike, d.oiVelocity);

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
          <input type="range" class="sim-ctrl-slider" id="sim-${cfg.id}-slider" min="${cfg.min}" max="${cfg.max}" step="${cfg.step}" value="${value}" oninput="${cfg.overrideVar}=parseFloat(this.value);app.simulator.gexScenarioDirty=true;simUpdate()">
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
    if(app.strategy.selectionSymbol !== d.symbol){
      app.strategy.selectionSymbol = d.symbol || null;
      app.strategy.selectionTouched = false;
    }
    if(!app.strategy.selectionTouched){
      const decisionStrategy = String((d.decision && (d.decision.suggestedStrategy
        || (d.decision.autoStrategy && d.decision.autoStrategy.name))) || '').trim().toLowerCase();
      const recommendedIdx = decisionStrategy
        ? strats.findIndex(s => String(s.name || '').trim().toLowerCase() === decisionStrategy)
        : -1;
      if(recommendedIdx >= 0) _selStratIdx = recommendedIdx;
    }
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

  stratSimulatorHtml+=`<div class="strategy-simulator-grid" style="display:grid;grid-template-columns:${strats.length?'1fr 1fr':'1fr'};gap:16px;margin-bottom:18px;align-items:stretch;">

    ${strats.length ? `
    <!-- LEFT: Strategy Payoff -->
    <div id="sec-strats" class="section-card sc-amber" style="min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;">

      <div class="section-header strat-focus-header" role="button" tabindex="0"
           aria-label="Open Strategy Payoff full view"
           onclick="if(!event.target.closest('button,select,input'))openStratPayoffModal()"
           onkeydown="if((event.key==='Enter'||event.key===' ')&&!event.target.closest('button,select,input')){event.preventDefault();openStratPayoffModal();}">
        <span class="section-title"><span class="section-icon">🎯</span>Strategy Payoff</span>
        <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
      </div>

      <!-- Dropdowns row -->
      <div style="display:flex;gap:8px;margin-bottom:10px;">
        <select id="strat-select" onchange="onStrategyPicked(this.value)" style="
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
      <div class="chart-expand-wrap" role="button" tabindex="0" aria-label="Expand Strategy Payoff chart" onclick="openStratPayoffChartModal()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openStratPayoffChartModal();}" title="Click to expand chart" style="cursor:zoom-in;background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 14px 10px;position:relative;">
        <span class="chart-expand-icon" title="Expand">⤢</span>
        <canvas id="strat-payoff-canvas" role="img" aria-label="Strategy profit and loss payoff in Indian rupees by underlying price" style="width:100%;display:block;" height="280">Strategy payoff values are available in the strategy summary.</canvas>
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

      <div class="sim-header sim-focus-header" role="button" tabindex="0"
           aria-label="Open Scenario — Institutional F&O Simulator full view"
           onclick="if(!event.target.closest('button,select,input'))openSimulatorFocusModal()"
           onkeydown="if((event.key==='Enter'||event.key===' ')&&!event.target.closest('button,select,input')){event.preventDefault();openSimulatorFocusModal();}">
        <div class="sim-title">Scenario — Institutional F&amp;O Simulator</div>
        <span class="sim-header-actions">
          <button type="button" class="btn btn-sm" onclick="event.stopPropagation();resetScenario()" aria-label="Reset scenario inputs to current live references" title="Reset scenario inputs only; live data is not reloaded">Reset to Live</button>
          <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
        </span>
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
          <div class="sim-chart-label"><span id="sim-gex-title">Live Net GEX Profile ($B)</span> &#8593; <span id="sim-gex-scope" style="text-transform:none;font-weight:500;color:var(--text-tertiary);letter-spacing:0;font-size:10px;">(Live Baseline)</span></div>
          <canvas id="sim-gex-canvas" role="img" aria-label="Scenario-adjusted net gamma exposure by strike" height="180">Scenario gamma values are available in the surrounding scenario analysis.</canvas>
          <div class="sim-annot" id="sim-annot"></div>
        </div>

        <!-- Dealer Regime bar — Dealer Bias dropdown sits at the right end
             of this same line (after the regime value), since it's the
             control that drives this readout. -->
        <div class="sim-regime-bar" id="sim-regime-bar">
          <span class="sim-regime-label" id="sim-regime-label">Live Dealer Regime</span>
          <div class="sim-regime-track" id="sim-regime-track"><div class="sim-regime-needle" id="sim-regime-needle" style="left:50%;"></div></div>
          <span class="sim-regime-val" id="sim-regime-val">Balanced</span>
          <select class="sim-dealer-sel" id="sim-dealer-sel" onchange="_simDealerOverride=this.value;app.simulator.gexScenarioDirty=true;simUpdate()" style="flex:none;flex-shrink:0;margin-left:8px;width:12ch;max-width:12ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
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
            <div class="sim-stat-label" id="sim-stat-gex-label">Live Net GEX ($B)</div>
            <div class="sim-stat-val" id="sim-stat-gex" style="color:${totalGEX>=0?'var(--blue)':'var(--red)'};">${fmtN(totalGEX,2)}</div>
            <div class="sim-stat-sub">${totalGEX>=0?'Scenario: long gamma (dampens)':'Scenario: short gamma (amplifies)'}</div>
          </div>
          <div class="sim-stat">
            <div class="sim-stat-label">Scenario Vanna Multiplier</div>
            <div class="sim-stat-val" id="sim-stat-vanna" style="color:var(--amber);">${fmtN(vannaMultiplier,2)}</div>
            <div class="sim-stat-sub">IV-flow amplifier</div>
          </div>
          <div class="sim-stat">
            <div class="sim-stat-label" id="sim-stat-flip-label">Live Gamma Flip</div>
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

  h += '</section>';

  // ── ZONE: CAPITAL FLOW ──
  // Left owns capital/OI flow. Right stacks participant cash flow with
  // Vol/OI Velocity and its derived block-print summary. Keeping #sdt-panel
  // separate preserves its interactive subtree across live refreshes.
  h += `<section id="workspace-flow" class="dashboard-workspace" data-dashboard-workspace="flow"${activeWorkspace==='flow'?'':' hidden'}><div id="oi-flow-section">

  <div id="zone-capital-flow" class="zone-divider zone-divider--primary"><span class="zone-divider-title">Participation Evidence</span><span class="zone-divider-subtitle">Institutional activity and unusual block participation</span></div>
  <div class="capital-flow-story">
    <div class="capital-flow-support-grid" aria-label="Supporting flow evidence">
      ${buildFiiDiiSummaryCard(d)}
      <div id="sdt-panel" class="section-card sc-neutral velocity-summary-card">
        <button class="section-header nav-card-header" onclick="openVolOiVelocityModal()"
           aria-label="Open Vol/OI Velocity by Strike — view block-detection chart" title="Open the block-detection chart">
          <span class="section-title nav-card-header-label"><span class="section-icon">⚡</span>Vol/OI Velocity by Strike <span style="text-transform:none;font-weight:500;color:var(--text-tertiary);letter-spacing:0;">(Block Detection)</span></span>
          <span class="nav-card-header-arrow" aria-hidden="true">↗</span>
        </button>
        <div class="oi-flow-block-line" id="oi-flow-block-summary">Loading block-print scan…</div>
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
             The block-print summary remains directly below this header. -->

        <!-- Strike Detail table itself lives only in the Strike Detail
             Report modal now (opened via the "📄 Strike Detail Report →"
             button on the Institutional Activity Crux card below) — the
             inline collapse/expand version that used to render here was a
             duplicate and has been removed. simRenderTable() (simulator-
             view.js) still computes the rows/stats every tick and writes
             them directly into the modal's #sdt-rows/#sdt-stat-* elements;
             no inline element needed here to do so. -->
      </div>
    </div>
  </div>

  </div></section>`;

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
  h += `<section id="workspace-institutional" class="dashboard-workspace" data-dashboard-workspace="institutional"${activeWorkspace==='institutional'?'':' hidden'}>`;
  h += '<div id="zone-institutional" class="zone-divider zone-divider--secondary"><span class="zone-divider-title">Institutional Activity</span><span class="zone-divider-subtitle">Large positioning and participant confirmation</span></div>';
  h += `<div class="institutional-crux-grid">
    ${greeksMoneynessHtml}
    ${app.exec.buildInstitutionalActivitySummaryCard(d)}
  </div>`;
  h += `<details class="card" id="institutional-detail-card">
    <summary>
      <div class="card-head"><span class="ic">🏦</span>Institutional Positioning Detail<span class="fill"></span></div>
      <span class="chev">▶</span>
    </summary>
    <div class="detail-body">${app.exec.renderInstitutionalGrid(d)}</div>
  </details>`;
  h += '</section>';

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
  h += `<section id="workspace-validation" class="dashboard-workspace" data-dashboard-workspace="validation"${activeWorkspace==='validation'?'':' hidden'}>`;
  h += '<div id="zone-confirmation" class="zone-divider zone-divider--tertiary"><span class="zone-divider-title">Strategy, Risk &amp; Validation</span><span class="zone-divider-subtitle">Volatility, probability and scenario tools</span></div>';
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
  h += '</section>';


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
              || isCardClickPending('greeksMoneyness')
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
    const focusHost = document.getElementById('strategy-focus-host');
    const focusOpen = document.getElementById('strat-payoff-modal')?.classList.contains('open');
    if(focusOpen && focusHost){
      if(fresh) fresh.remove();
      focusHost.appendChild(_oldStratsSection);
    }else if(fresh && fresh.parentNode){
      fresh.parentNode.replaceChild(_oldStratsSection, fresh);
    }
  }
  if(_oldGreeksMoneySect){
    const fresh = document.getElementById('sec-greeks-moneyness');
    if(fresh && fresh.parentNode) fresh.parentNode.replaceChild(_oldGreeksMoneySect, fresh);
  }
  if(_oldSimSection){
    const fresh = document.getElementById('sec-simulator');
    const focusHost = document.getElementById('simulator-focus-host');
    const focusOpen = document.getElementById('simulator-focus-modal')?.classList.contains('open');
    if(focusOpen && focusHost){
      if(fresh) fresh.remove();
      focusHost.appendChild(_oldSimSection);
    }else if(fresh && fresh.parentNode){
      fresh.parentNode.replaceChild(_oldSimSection, fresh);
    }
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
  if(isModalOpen('greeks-dashboard-modal')) renderGreeksGex(_grkView);
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
  if(isModalOpen('iv-surface-modal')) this.renderIvSurfaceModal();
  this._bindDecisionDetailGuard();
  // Same click-guard the incremental per-tick refresh binds after each of
  // its own outerHTML swaps (see chain-renderer.js's chainSummaryEl /
  // instActivityEl blocks) — bound here too so the very first tick after
  // this full rebuild is already protected, not just ticks after the
  // first incremental swap.
  bindCardClickGuard(document.getElementById('chain-summary-card'), 'chainSummary');
  bindCardClickGuard(document.getElementById('sec-greeks-moneyness'), 'greeksMoneyness');
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
  if(dteDisplay) dteDisplay.textContent = '· ' + (d.dte||0) + 'd';
  if(timeDisplay) timeDisplay.textContent = d.refreshTime || '--';
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if(window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(d, true);
    if(window.resizeGreeksMoneynessChart) window.resizeGreeksMoneynessChart('greeksChart');
    // Scenario P&L bar chart (scenario-analysis-view.js) — same
    // full-rebuild hook as Greeks by Moneyness above, `force:true` since
    // this is a fresh canvas from the full-page renderDashboard() pass.
    if(window.updateScenarioPnlChart) window.updateScenarioPnlChart(d, true);
    // Card is collapsed <details> by default, so also (re)bind the
    // open-toggle resize listener — a fresh canvas from this full rebuild
    // has no listener attached yet, and without it the chart stays stuck
    // at whatever 0×0 box it first measured while the card was closed.
    if(window.bindScenarioPnlChartToggle) window.bindScenarioPnlChartToggle();
  }));
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
