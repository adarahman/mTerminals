// ============================================================
// metrics.js
// IA redesign step 6 — shared Greeks/GEX metric helpers.
//
// Pulls three pieces of logic that were previously duplicated verbatim
// across chain-greeks.js, chain-renderer.js, conviction-gauge.js, and
// simulator-view.js into one place:
//
//   computeNetGEX(greeks)            — sum of netGEX across a greeks array
//   computeGammaFlip(greeks, refPrice) — thin wrapper around
//                                        findGammaFlipStrike (chain-helpers.js)
//   getVisibleRangeGreeks(d, filteredChain) — filters d.greeks down to the
//                                        strikes present in filteredChain
//
// Load position: after chain-helpers.js (needs findGammaFlipStrike), before
// every view file that consumes it (chain-view.js, conviction-gauge.js,
// chain-renderer.js, chain-greeks.js, simulator-view.js). See
// DashboardPro.html's script order comment above this file's <script> tag.
//
// Plain global functions (no classes), matching every other helper file's
// convention (chain-helpers.js, formatters.js) — not an ES module.
// ============================================================

// Sums netGEX across whatever scope of the greeks array the caller passes
// in (visible-range, whole-chain, scenario-adjusted, etc.) — this function
// doesn't decide scope, callers do. Was previously
// `greeks.reduce((s,g)=>s+(g.netGEX||0),0)`, duplicated in
// buildGreeksAlertsHtml/renderGreeksGex (chain-greeks.js) and
// renderDashboard/renderGEX (chain-renderer.js).
function computeNetGEX(greeks) {
  return (greeks || []).reduce((sum, g) => sum + (g.netGEX || 0), 0);
}

// Wraps findGammaFlipStrike (chain-helpers.js) so every caller goes through
// one name instead of some calling findGammaFlipStrike directly and others
// duplicating its logic. refPrice (spot or ATM strike) is optional, same as
// findGammaFlipStrike itself — see that function's own header comment for
// why passing it matters when a chain has more than one zero-crossing.
function computeGammaFlip(greeks, refPrice) {
  return findGammaFlipStrike(greeks, refPrice);
}

// Filters d.greeks (the full, unfiltered greeks array) down to just the
// strikes present in filteredChain (typically getFilteredChain(d)'s output)
// — the same visible-range scope as the Greeks/Net GEX Alerts card, the
// Greeks modal, Smart Money Ranking, and the Conviction Gauge's pillars.
// filteredChain is optional: chain-renderer.js/conviction-gauge.js already
// have a filtered chain in hand and pass it in directly to avoid recomputing
// it; exec-view.js's buildExecutiveDashboardCards doesn't, and calls this
// with just `d` — falling back to getFilteredChain(d) here (rather than
// requiring every caller to pass it) means an omitted argument produces the
// SAME visible-range scope every other caller gets, instead of silently
// returning [] (empty strikeSet) and having the Greeks Alerts card render
// a false "neutral gamma" reading with no error anywhere.
// Was previously duplicated as
// `(d.greeks||[]).filter(g=>strikeSet.has(g.strike))` in chain-greeks.js's
// renderGreeksGex and chain-renderer.js's renderDashboard/renderGEX, each
// building its own copy of the strike Set.
function getVisibleRangeGreeks(d, filteredChain) {
  const chain = filteredChain || getFilteredChain(d);
  const strikeSet = new Set((chain || []).map(c => c.strike));
  return ((d && d.greeks) || []).filter(g => strikeSet.has(g.strike));
}

// ── PCR (dashboard-redesign-proposal.md §4) ──
// Range OI totals + Range PCR — genuinely computed on the frontend,
// independently of Full Chain PCR, from whatever chain array the caller
// passes in (typically getFilteredChain(d)'s range-filtered output).
// Returns totalCe/totalPe alongside pcr (not just the ratio) because
// buildVolOiDetailHtml needs the raw totals themselves as vol-ratio
// denominators — pcr is unused there, totalCe/totalPe are unused nowhere,
// so one shape covers both callers instead of two overlapping functions.
// Was previously duplicated inline in chain-template.js's
// buildChainSummaryHtml AND buildVolOiDetailHtml:
//   const totalCe = chain.reduce((s,r)=>s+(r.ceOI||0),0);
//   const totalPe = chain.reduce((s,r)=>s+(r.peOI||0),0);
//   const pcr = totalPe/(totalCe||1);
// Pass the SAME range-filtered chain Range PCR's card already uses — this
// function doesn't decide scope any more than computeNetGEX does.
function computeRangeChainTotals(chain) {
  const totalCe = (chain || []).reduce((s, r) => s + (r.ceOI || 0), 0);
  const totalPe = (chain || []).reduce((s, r) => s + (r.peOI || 0), 0);
  const pcr = totalPe / (totalCe || 1);
  return { totalCe, totalPe, pcr };
}
