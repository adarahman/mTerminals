// ============================================================
// chain-utils.js
// Phase 2 chain-view decomposition — see chain-view.js's header comment
// for the full split rationale and load-order requirement (this file
// must load after chain-view.js, and before dashboard.js).
//
// This file holds small chain-view-local formatting helpers. Shared
// chain/expiry/index domain helpers used across the whole dashboard
// (fmt/fmtN/fmtK, chainCombinedSignal, activeAtm, etc.) still live in
// formatters.js / chain-helpers.js — nothing there moved. Moved verbatim
// from chain-views.js.
// ============================================================

ChainDenseView.prototype.tickFill = function(clr) {
    return `repeating-linear-gradient(90deg, ${clr} 0px, ${clr} 2px, transparent 2px, transparent 4px)`;
};
