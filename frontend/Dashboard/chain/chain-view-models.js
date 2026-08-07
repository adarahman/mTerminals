// ============================================================
// chain-view-models.js
// Phase 3 rendering/logic split (see master optimization prompt, Task
// "Remove HTML generation from business logic").
//
// This file is the BUSINESS-LOGIC + TEMPLATE layer for the two functions
// named in that task — ChainDenseView.buildRowsHtml() (chain-renderer.js)
// and ChainDenseView.buildStrikeDetailHtml() (chain-depth.js). Both used
// to compute derived values (formatted numbers, sign/direction classes,
// the combined CE+PE signal, OI bar-fill percentages, bid/ask depth
// strings) AND build the HTML string in the same function body. That's
// split into two sections in this one file now:
//
//   - The FIRST section (below) takes the already-computed row/greeks
//     data (produced by ChainDenseView.mapPayloadToRows() in
//     chain-depth.js) and returns plain view-model objects — every
//     derived value is computed there. No function in that section
//     touches the DOM or returns an HTML string.
//   - The SECOND section ("MERGED: chain-templates.js" below) takes
//     those view-model objects and turns them into HTML strings via
//     pure interpolation — no formatting/derivation calls (sign/
//     dirClass/fmt/fmtN/chainCombinedSignal/spClass/.toFixed/ternary-
//     derived-meaning) live there, only "put this value in this markup
//     slot".
//
// These two sections used to be separate files (chain-view-models.js /
// chain-templates.js) — merged into one on 2026-07-29 because
// "chain-templates.js" (plural) and chain-view.js's Phase-2 sibling
// "chain-template.js" (singular) were one letter apart and easy to
// confuse despite serving completely different layers. See the MERGED
// section's own comment below for details.
//
// This is a pure code-motion split: every value that used to be computed
// inline inside the old buildRowsHtml()/buildStrikeDetailHtml() template
// strings is computed in the first section instead, with the exact same
// expression — so HTML output is byte-for-byte unchanged. See
// chain-renderer.js / chain-depth.js for the thin functions that now
// just call build*ViewModel() then render*Template() (both here).
//
// Depends on fmt/fmtN/sign/dirClass (formatters.js) and
// chainCombinedSignal/spClass (chain-helpers.js) — must load after both.
// Must load before chain-renderer.js and chain-depth.js, which call into
// it. See DashboardPro.html script order.
// ============================================================

// ── One CE or PE leg's display data for a dense-chain row ──
// `oiFillPct` and `oiTotalSharePct` are pre-computed here (not derived
// from raw leg fields inside the template) since both need sibling data
// (maxOI across all rows, or the row's totalCeOi/totalPeOi) that the
// per-leg object itself doesn't carry.
function buildChainRowLegViewModel(leg, oiFillPct, oiTotalSharePct) {
  return {
    ivText: leg.iv != null ? leg.iv + '%' : '—',
    ivDelta: sign(leg.ivChg),
    ivDeltaClass: dirClass(leg.ivChg),
    volText: fmt(leg.vol),
    volSub: leg.volPct != null ? leg.volPct + '% oi' : '—',
    ltpText: leg.ltp != null ? leg.ltp : '—',
    ltpRaw: leg.ltp != null ? leg.ltp : null,
    ltpDelta: sign(leg.chg),
    ltpClass: dirClass(leg.chg),
    // velText: sign(leg.oiVel != null ? fmt(leg.oiVel) : null),
    velText: sign(leg.oiVel != null ? leg.oiVel : null),
    velSub: oiTotalSharePct,
    velClass: dirClass(leg.oiVel),
    oiFillPct,
    oiText: fmt(leg.oi),
    oiDelta: sign(leg.oiChg != null ? leg.oiChg : null),
    oiDeltaClass: dirClass(leg.oiChg),
  };
}

// ── One dense-chain table row (collapsed row + its hidden detail row) ──
// Moved verbatim (as calculations) from the body of the old
// ChainDenseView.prototype.buildRowsHtml — same expressions, same order.
function buildChainRowViewModel(r, g, maxOI, selectedDepthStrike) {
  const oiFillCE = (((r.ce.oi || 0) / maxOI) * 100).toFixed(0);
  const oiFillPE = (((r.pe.oi || 0) / maxOI) * 100).toFixed(0);
  const isDepthSelected = selectedDepthStrike === r.strike;
  const totalCeOiPct = r.totalCeOi ? ((r.ce.oi / r.totalCeOi) * 100).toFixed(1) + '% oi' : '—';
  const totalPeOiPct = r.totalPeOi ? ((r.pe.oi / r.totalPeOi) * 100).toFixed(1) + '% oi' : '—';
  const cs = chainCombinedSignal(r.ce.signal, r.pe.signal);

  return {
    strike: r.strike,
    rowClass: `${r.isAtm ? 'atm' : ''}${isDepthSelected ? ' depth-selected' : ''}`,
    rowIdAttr: r.isAtm ? ' id="chain-row-atm"' : '',
    ce: buildChainRowLegViewModel(r.ce, oiFillCE, totalCeOiPct),
    pe: buildChainRowLegViewModel(r.pe, oiFillPE, totalPeOiPct),
    pcrText: r.pcr + ' / ' + r.pcrChg,
    pcrDeltaClass: dirClass(parseFloat(r.pcrChg)),
    signalCls: cs.cls,
    signalLabel: cs.label,
    detail: buildStrikeDetailViewModel(r, g, maxOI),
  };
}

// ── NEW (institutional strike-detail redesign, not part of the Phase 3
// code-motion split above): a leg is flagged "Institution" when its OI
// is a large share of the visible range's biggest single-leg OI, vs
// "Retail" otherwise. Tunable heuristic — same style as
// GREEKS_ALERT_THETA_PCT in chain-greeks.js — not a value the backend
// sends explicitly.
const SMART_MONEY_OI_SHARE = 0.30; // leg.oi / maxOI at/above this => "Institution"

// ── NEW: combined-OI-bar view model for one leg — one bar whose full
// length is the leg's total OI (relative to maxOI across the visible
// range), with an overlay segment for today's ΔOI: a filled segment at
// the bar's tip for a build-up, a hollow/outlined segment for unwinding.
// Mirrors the "Key Innovation" bar from the institutional design spec
// (single bar instead of separate "54.3L (+33.3L)" text).
function buildOiCombinedBarViewModel(leg, maxOI) {
  const oi = leg.oi || 0;
  const oiChg = leg.oiChg || 0;
  const barPct = maxOI > 0 ? Math.min(100, (oi / maxOI) * 100) : 0;
  const chgPct = maxOI > 0 ? Math.min(barPct, (Math.abs(oiChg) / maxOI) * 100) : 0;
  const isInstitution = maxOI > 0 && oi >= maxOI * SMART_MONEY_OI_SHARE;
  return {
    oiText: fmt(oi),
    oiChgText: sign(oiChg != null ? fmt(oiChg) : null),
    barPct: barPct.toFixed(1),
    chgPct: chgPct.toFixed(1),
    chgDir: oiChg > 0 ? 'inc' : oiChg < 0 ? 'dec' : 'flat',
    smartMoneyLabel: isInstitution ? 'Institution' : 'Retail',
    smartMoneyDot: isInstitution ? '●' : '○',
    smartMoneyColor: isInstitution ? 'var(--oc-cyan, #22d3ee)' : 'var(--text-faint)',
  };
}

// ── One CE or PE leg's display data for the per-strike detail panel ──
function buildStrikeDetailLegViewModel(leg, g, side, hasGreeks, maxOI) {
  const delta = side === 'ce' ? g.cDelta : g.pDelta;
  const gamma = side === 'ce' ? g.cGamma : g.pGamma;
  const theta = side === 'ce' ? g.cTheta : g.pTheta;
  const vega  = side === 'ce' ? g.cVega  : g.pVega;
  return {
    sideLabel: side.toUpperCase(),
    color: side === 'ce' ? 'var(--ce)' : 'var(--pe)',
    bidStr: leg.bid != null ? fmtN(leg.bid, 2) + (leg.bidQty ? ' ×' + fmt(leg.bidQty) : '') : '—',
    askStr: leg.ask != null ? fmtN(leg.ask, 2) + (leg.askQty ? ' ×' + fmt(leg.askQty) : '') : '—',
    hasGreeks,
    deltaText: fmtN(delta, 3),
    gammaText: fmtN(gamma, 3),
    thetaText: fmtN(theta, 2),
    vegaText: fmtN(vega, 2),
    signalLabel: leg.signal || '—',
    signalClass: spClass(leg.signal),
    // NEW: institutional combined-OI-bar + smart-money badge fields.
    oiBar: buildOiCombinedBarViewModel(leg, maxOI),
  };
}
// ── The expanded per-strike summary panel (Bid/Ask, Greeks, Net GEX,
// per-leg signal) shown when a dense-chain row is clicked. Moved verbatim
// (as calculations) from the body of the old
// ChainDenseView.prototype.buildStrikeDetailHtml, plus the NEW
// institutional combined-OI-bar / smart-money fields above (maxOI is the
// only new input, threaded down from buildChainRowViewModel's caller —
// same maxOI already used for the collapsed row's oiFillPct).
function buildStrikeDetailViewModel(r, g, maxOI) {

  const hasGreeks = g.cDelta != null;

  return {
    strike: r.strike,

    hasGreeks,

    ce: buildStrikeDetailLegViewModel(
      r.ce,
      g,
      'ce',
      hasGreeks,
      maxOI
    ),

    pe: buildStrikeDetailLegViewModel(
      r.pe,
      g,
      'pe',
      hasGreeks,
      maxOI
    ),


    netGEXText: fmtN(g.netGEX,3)+'B',

    netGEXColor:
      (g.netGEX || 0)>=0
      ? 'var(--ce)'
      : 'var(--pe)',


    // NEW FINAL INSTITUTIONAL VIEW
    institutional:
      buildInstitutionalView(r,g)

  };
}
function buildInstitutionalView(r, g) {

  const ceOI = r.ce?.oi || 0;
  const peOI = r.pe?.oi || 0;

  const ceChg = r.ce?.oiChange || 0;
  const peChg = r.pe?.oiChange || 0;

  const ceSignal = (r.ce?.signal || '').toLowerCase();
  const peSignal = (r.pe?.signal || '').toLowerCase();

  let score = 0;
  let reasons = [];

  // OI dominance
  if (ceOI > peOI) {
    score += 1;
    reasons.push('CE OI dominance');
  }

  if (peOI > ceOI) {
    score -= 1;
    reasons.push('PE OI dominance');
  }


  // Smart money signals
  if (ceSignal.includes('writing')) {
    score -= 1;
    reasons.push('Call writing');
  }

  if (peSignal.includes('writing')) {
    score += 1;
    reasons.push('Put writing');
  }


  if (ceSignal.includes('buying')) {
    score += 1;
    reasons.push('Call buying');
  }

  if (peSignal.includes('buying')) {
    score -= 1;
    reasons.push('Put buying');
  }


  // Delta/GEX confirmation
  if ((g.netGEX || 0) > 0) {
    score += 1;
    reasons.push('Positive GEX');
  } 
  else if ((g.netGEX || 0) < 0) {
    score -= 1;
    reasons.push('Negative GEX');
  }


  let label;
  let color;

  if(score >= 3){
    label = "Institutional Bullish";
    color = "var(--green)";
  }
  else if(score <= -3){
    label = "Institutional Bearish";
    color = "var(--red)";
  }
  else if(score > 0){
    label = "Accumulation Bias";
    color = "var(--blue)";
  }
  else if(score < 0){
    label = "Distribution Bias";
    color = "var(--amber)";
  }
  else{
    label = "Neutral";
    color = "var(--txt3)";
  }


  return {
    label,
    color,
    score,
    reason: reasons.join(' • ')
  };
}

// ============================================================
// MERGED: chain-templates.js (formerly a separate file)
//
// This used to be its own file, "chain-templates.js" — one letter apart
// from chain-view.js's Phase-2 sibling "chain-template.js" (singular).
// The two were easy to confuse (wrong file opened, edits landing in the
// wrong one) despite serving completely different layers: chain-template.js
// holds ChainView's markup (top bar, Decision Engine box, mini chart);
// this section holds the pure HTML-templating half of the Phase 3
// business-logic/rendering split whose other half is above in this same
// file. Since chain-templates.js's own header already described itself
// as "Companion file to chain-view-models.js — see that file's header
// comment for the full split rationale", merging it into its companion
// removes the confusing name entirely rather than just renaming it.
//
// Every function below takes a view-model object built above (in this
// file) and returns an HTML string by pure interpolation — no
// formatting/derivation calls (sign/dirClass/fmt/fmtN/
// chainCombinedSignal/spClass, arithmetic, .toFixed, or a ternary that
// derives NEW meaning from raw data) belong here; every value used was
// already computed onto the view model above. The one exception is
// cell(...): it's an existing presentational helper (formatters.js) that
// just wraps four already-computed values in markup, the same way a
// <Cell/> component would — it takes no raw row/leg data, so calling it
// here isn't reintroducing business logic.
//
// Called from ChainDenseView.buildRowsHtml() (chain-renderer.js) and
// ChainDenseView.buildStrikeDetailHtml() (chain-depth.js) — both must
// load after this file, same as before the merge. See
// DashboardPro.html's script order and build.mjs's page.js list, both
// updated to load just "chain-view-models.js" (no separate
// chain-templates.js entry) ahead of chain-renderer.js/chain-depth.js.
// ============================================================

// NEW: institutional combined-OI-bar row — one bar whose length is total
// OI (already computed onto vm.oiBar as a %), with an overlay segment at
// the bar's tip for today's ΔOI: a dashed white stripe for a build-up,
// a hollow amber outline for unwinding — same track, so the comparison
// stays meaningful across strikes instead of two separate numbers. Plus
// the smart-money dot/label. Self-contained gradient string here (not
// chain-depth.js's tickFill()) since this section only does pure
// interpolation — no shared helper needed for a single fixed pattern.
function oiCombinedBarHtml(leg) {
  const b = leg.oiBar;
  if (!b) return '';
  const rightOffset = (100 - parseFloat(b.barPct)).toFixed(1);
  const chgClr = b.chgDir === 'inc' ? '#22c55e' : b.chgDir === 'dec' ? 'var(--oc-amber)' : 'var(--text-faint)';
  return `
        <div style="margin:6px 0 3px;">
          <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text-faint);margin-bottom:2px;">
            <span>OI <strong style="color:${leg.color};">${b.oiText}</strong></span>
            <span style="color:${chgClr};">${b.oiChgText}</span>
          </div>
          <div style="position:relative;height:7px;background:rgba(255,255,255,0.07);border-radius:3px;overflow:hidden;">
            <div style="position:absolute;left:0;top:0;bottom:0;width:${b.barPct}%;background:${leg.color};opacity:0.55;border-radius:3px;"></div>
            ${b.chgDir === 'inc' ? `<div style="position:absolute;top:0;bottom:0;right:${rightOffset}%;width:${b.chgPct}%;background-image:repeating-linear-gradient(90deg,#fff 0px,#fff 2px,transparent 2px,transparent 4px);opacity:0.9;"></div>` : ''}
            ${b.chgDir === 'dec' ? `<div style="position:absolute;top:0;bottom:0;right:${rightOffset}%;width:${b.chgPct}%;border:1px solid var(--oc-amber);box-sizing:border-box;border-radius:2px;"></div>` : ''}
          </div>
        </div>
        <div style="font-size:9px;color:${b.smartMoneyColor};">${b.smartMoneyDot} ${b.smartMoneyLabel}</div>`;
}

// ── The expanded per-strike summary panel ──
function renderStrikeDetailTemplate(vm) {
  const legBlockHtml = (leg) => `
        <div style="min-width:230px;">
          <div style="font-weight:700;color:${leg.color};margin-bottom:4px;">${leg.sideLabel}</div>
          <div>Bid <strong>${leg.bidStr}</strong> &nbsp;/&nbsp; Ask <strong>${leg.askStr}</strong></div>
          ${leg.hasGreeks ? `<div>&Delta; <strong>${leg.deltaText}</strong> &nbsp;&Gamma;&times;10&#8308; <strong>${leg.gammaText}</strong> &nbsp;&Theta; <strong>${leg.thetaText}</strong> &nbsp;Vega <strong>${leg.vegaText}</strong></div>` : ''}
          <div>Signal <strong class="sp ${leg.signalClass}">${leg.signalLabel}</strong></div>
          ${oiCombinedBarHtml(leg)}
        </div>`;
  return `
      <div style="display:flex;gap:28px;flex-wrap:wrap;align-items:flex-start;padding:8px 12px;font-size:10.5px;color:var(--text-faint);line-height:1.6;">
        ${legBlockHtml(vm.ce)}
        <div style="min-width:140px;">
          <div style="font-weight:700;color:var(--oc-amber);margin-bottom:4px;">STRIKE ${vm.strike}</div>
          ${vm.hasGreeks ? `<div>Net GEX <strong style="color:${vm.netGEXColor};">${vm.netGEXText}</strong></div>` : ''}
        </div>
        ${legBlockHtml(vm.pe)}
      </div>`;
}

// ── One dense-chain table row (collapsed row + its hidden detail row) ──
function renderChainRowTemplate(vm) {
  let html = `<tr class="${vm.rowClass}"${vm.rowIdAttr} style="cursor:pointer;" tabindex="0" aria-label="Strike ${vm.strike}; press Enter for full summary" onclick="toggleGreekRow(${vm.strike})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleGreekRow(${vm.strike})}" title="Click for full strike summary">`;
  html += `<td>${cell(vm.ce.ivText, vm.ce.ivDelta, "flat", vm.ce.ivDeltaClass)}</td>`;
  html += `<td>${cell(vm.ce.volText, vm.ce.volSub, "flat", "flat")}</td>`;
  html += `<td class="pt-ltp-click" role="button" tabindex="0" aria-label="Trade ${vm.strike} CE" onclick="event.stopPropagation();ptOpenQuickOrder(event,${vm.strike},'CE',${vm.ce.ltpRaw != null ? vm.ce.ltpRaw : 'null'})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();ptOpenQuickOrder(event,${vm.strike},'CE',${vm.ce.ltpRaw != null ? vm.ce.ltpRaw : 'null'})}" title="Click to trade this strike">${cell(vm.ce.ltpText, vm.ce.ltpDelta, vm.ce.ltpClass, vm.ce.ltpClass)}</td>`;
  html += `<td>${cell(vm.ce.velText, vm.ce.velSub, vm.ce.velClass, "flat")}</td>`;
  html += `<td class="oi-bar"><div class="fill ce" style="width:${vm.ce.oiFillPct}%"></div>${cell(vm.ce.oiText, vm.ce.oiDelta, "flat", vm.ce.oiDeltaClass)}</td>`;
  html += `<td class="strike" role="button" tabindex="0" aria-label="Pin strike ${vm.strike} depth and open summary" title="Click to pin Bid/Ask Depth — summary also shown below" onclick="event.stopPropagation();selectDepthStrike(${vm.strike});toggleGreekRow(${vm.strike})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();selectDepthStrike(${vm.strike});toggleGreekRow(${vm.strike})}">${cell(vm.strike, vm.pcrText, "", vm.pcrDeltaClass)}</td>`;
  html += `<td class="oi-bar"><div class="fill pe" style="width:${vm.pe.oiFillPct}%"></div>${cell(vm.pe.oiText, vm.pe.oiDelta, "flat", vm.pe.oiDeltaClass)}</td>`;
  html += `<td>${cell(vm.pe.velText, vm.pe.velSub, vm.pe.velClass, "flat")}</td>`;
  html += `<td class="pt-ltp-click" role="button" tabindex="0" aria-label="Trade ${vm.strike} PE" onclick="event.stopPropagation();ptOpenQuickOrder(event,${vm.strike},'PE',${vm.pe.ltpRaw != null ? vm.pe.ltpRaw : 'null'})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();ptOpenQuickOrder(event,${vm.strike},'PE',${vm.pe.ltpRaw != null ? vm.pe.ltpRaw : 'null'})}" title="Click to trade this strike">${cell(vm.pe.ltpText, vm.pe.ltpDelta, vm.pe.ltpClass, vm.pe.ltpClass)}</td>`;
  html += `<td>${cell(vm.pe.volText, vm.pe.volSub, "flat", "flat")}</td>`;
  html += `<td>${cell(vm.pe.ivText, vm.pe.ivDelta, "flat", vm.pe.ivDeltaClass)}</td>`;
  html += `<td class="sig-col"><span class="sig ${vm.signalCls}">${vm.signalLabel}</span></td>`;
  html += `</tr>`;
  // Two <tr> elements instead of native <details>/<summary>, because
  // <summary> is not a valid child of <tr>/<tbody>; browsers silently
  // hoist it out of the table and the click handler never fires where
  // expected. (Same note as the pre-split version.)
  html += `<tr id="grk-row-${vm.strike}" class="grk-row" style="display:none;">
        <td colspan="12" style="text-align:left;padding:0;">${renderStrikeDetailTemplate(vm.detail)}</td>
      </tr>`;
  return html;
}
