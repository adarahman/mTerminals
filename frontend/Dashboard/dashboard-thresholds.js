// ============================================================
// dashboard-thresholds.js
// Split out of panels-views.js (previously the first ~22 lines of that
// file). Institutional Activity band thresholds shared by OiFlowView,
// ExecView, and SimulatorView — kept as its own file so it loads once,
// before any of the views that read it, without being buried inside
// whichever view file happened to define it first.
// ============================================================

// ── Institutional Activity band thresholds ──────────────────────────────
// Used by SimulatorView's Strike Detail table + Vol/OI Velocity bars, and
// by ExecView's Institutional Activity Crux summary card below, so every
// view of this data agrees on where the near/far line sits and how each
// band is scored.
//
// Near-ATM strikes carry naturally heavier OI/volume (retail chop lives
// here), so calling one "institutional" needs a bigger OI standout vs.
// the pack and tighter turnover — but the outright Vol/OI ratio needed to
// flag a "block" print can be lower, since a fast ratio change close to
// spot is itself a meaningful tell on its own.
// Far strikes (beyond the near band) are thin by default, so a smaller OI
// standout already means something — but thin books also see occasional
// one-off retail clip-ins, so the ratio value needed to call a print a
// "block" is raised to filter those out.
// INST_NEAR_BAND_STRIKES and instBandFor() now live in shared/market-structure.js
// (loaded before this file) so this dashboard and the Option Chain page
// can't drift out of sync on where "near" ends.
const INST_THRESHOLDS = {
  near: { oiMult: 1.75, volRatioMax: 40, blockVal: 1.2 },
  far:  { oiMult: 1.2,  volRatioMax: 55, blockVal: 1.8 },
};
