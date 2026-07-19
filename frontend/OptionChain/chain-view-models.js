// ============================================================
// chain-view-models.js
// Phase 3 rendering/logic split (see master optimization prompt, Task
// "Remove HTML generation from business logic").
//
// This file is the BUSINESS-LOGIC layer for the two functions named in
// that task — ChainDenseView.buildRowsHtml() (chain-renderer.js) and
// ChainDenseView.buildStrikeDetailHtml() (chain-depth.js). Both used to
// compute derived values (formatted numbers, sign/direction classes, the
// combined CE+PE signal, OI bar-fill percentages, bid/ask depth strings)
// AND build the HTML string in the same function body. That's split now:
//
//   - THIS FILE takes the already-computed row/greeks data (produced by
//     ChainDenseView.mapPayloadToRows() in chain-depth.js) and returns
//     plain view-model objects — every derived value is computed HERE.
//     No function in this file touches the DOM or returns an HTML string.
//   - chain-templates.js takes those view-model objects and turns them
//     into HTML strings via pure interpolation — no formatting/derivation
//     calls (sign/dirClass/fmt/fmtN/chainCombinedSignal/spClass/.toFixed/
//     ternary-derived-meaning) live there, only "put this value in this
//     markup slot".
//
// This is a pure code-motion split: every value that used to be computed
// inline inside the old buildRowsHtml()/buildStrikeDetailHtml() template
// strings is computed here instead, with the exact same expression — so
// HTML output is byte-for-byte unchanged. See chain-renderer.js /
// chain-depth.js for the thin functions that now just call
// build*ViewModel() (here) then render*Template() (chain-templates.js).
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
    velText: sign(leg.oiVel != null ? fmt(leg.oiVel) : null),
    velSub: oiTotalSharePct,
    velClass: dirClass(leg.oiVel),
    oiFillPct,
    oiText: fmt(leg.oi),
    oiDelta: sign(leg.oiChg != null ? fmt(leg.oiChg) : null),
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
    detail: buildStrikeDetailViewModel(r, g),
  };
}

// ── One CE or PE leg's display data for the per-strike detail panel ──
function buildStrikeDetailLegViewModel(leg, g, side, hasGreeks) {
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
  };
}

// ── The expanded per-strike summary panel (Bid/Ask, Greeks, Net GEX,
// per-leg signal) shown when a dense-chain row is clicked. Moved verbatim
// (as calculations) from the body of the old
// ChainDenseView.prototype.buildStrikeDetailHtml.
function buildStrikeDetailViewModel(r, g) {
  const hasGreeks = g.cDelta != null;
  return {
    strike: r.strike,
    hasGreeks,
    ce: buildStrikeDetailLegViewModel(r.ce, g, 'ce', hasGreeks),
    pe: buildStrikeDetailLegViewModel(r.pe, g, 'pe', hasGreeks),
    netGEXText: fmtN(g.netGEX, 3) + 'B',
    netGEXColor: (g.netGEX || 0) >= 0 ? 'var(--ce)' : 'var(--pe)',
  };
}
