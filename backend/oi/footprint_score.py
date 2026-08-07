"""
oi/footprint_score.py
------------------------
Phase C of the Institutional Positioning Analytics layer — spec items
#5 (Institutional Footprint Score) and #10 (Capital Concentration).

Both operate on the OUTPUT of oi.capital_metrics.compute_capital_metrics()
(a per-strike DataFrame), not raw master — same convention
compute_chain_metrics() uses, so callers don't pass master twice.

WHY PERCENTILE-RANK NORMALIZATION, NOT FIXED THRESHOLDS: capital_metrics.py's
compute_chain_metrics() docstring explicitly deferred a composite score
because "those weights are uncalibrated". A composite built on fixed
magnitude thresholds (e.g. "capital flow > Rs50L = 100 points") would be
just as uncalibrated and additionally wrong across symbols (BANKNIFTY's
strike-level capital is a different order of magnitude than MIDCPNIFTY's)
and across volatility regimes (a quiet expiry week vs a budget-day chain).
Ranking each strike against the OTHER STRIKES VISIBLE RIGHT NOW sidesteps
both problems — "loudest strike in today's chain" is well-defined without
a magnitude calibration step, at the cost of not being comparable
tick-to-tick (a strike's score can move purely because other strikes got
louder/quieter, not because it changed). That tradeoff is the right one
for a ranking product ("rank important strikes" is the spec's own
framing); it would be the wrong choice for an absolute trend line.

The component WEIGHTS below (0.25/0.20/0.15/0.15/0.15/0.10) are a
reasonable starting split reflecting the spec's own inputs, NOT
backtested or calibrated — same caveat as market_regime.py's thresholds.
Revisit once real chains show whether e.g. gamma is over- or under-
weighted relative to capital flow.

Futures OI is a spec input for #5 but is chain-wide (one number per
tick), not per-strike — it cannot rank strikes against each other, so it
is deliberately NOT one of the per-strike components here. It already
drives Market Regime / Smart Money Summary (market_regime.py,
smart_money_summary.py) at the chain level; folding it into a per-strike
score would just broadcast the same single number onto every strike,
diluting the per-strike signal without adding information.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["compute_footprint_score", "rank_footprint_strikes", "compute_capital_concentration"]

_WEIGHTS = {
    "capital_activity": 0.25,   # |ce_capital_flow| + |pe_capital_flow|
    "oi_change_activity": 0.20, # |ce_oi_chg| + |pe_oi_chg|
    "turnover_activity": 0.15,  # ce_premium_turnover + pe_premium_turnover (volume x price)
    "gamma_activity": 0.15,     # ce_gamma_exposure + pe_gamma_exposure
    "delta_activity": 0.10,     # |net_delta_exposure|
    "writing_activity": 0.15,   # OI added specifically via "Writing BuildUp" legs (ce_signal/pe_signal)
}


def compute_footprint_score(capital_df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes compute_capital_metrics()'s output. Returns a COPY with one new
    column, footprint_score (0-100, float), plus the six intermediate
    percentile-rank columns (footprint_pct_<component>) left in for
    transparency/debugging — a strike's score should be explainable by
    inspecting these, not just trusted as a black box.

    Empty/single-row input: percentile rank on a single row is degenerate
    (pandas gives it 1.0 / 100th percentile automatically, since it's both
    the min and max) — that's accepted here since it correctly reads
    "loudest strike in view" when there is exactly one strike in view,
    consistent with the module's own "ranked against what's visible now"
    design rather than a special-cased fallback.
    """
    if capital_df is None or capital_df.empty:
        return capital_df

    out = capital_df.copy()

    ce_signal = out["ce_signal"] if "ce_signal" in out.columns else pd.Series("", index=out.index)
    pe_signal = out["pe_signal"] if "pe_signal" in out.columns else pd.Series("", index=out.index)
    ce_writing_oi = out["ce_oi_chg"].where(ce_signal == "Writing BuildUp", 0.0)
    pe_writing_oi = out["pe_oi_chg"].where(pe_signal == "Writing BuildUp", 0.0)

    raw = pd.DataFrame({
        "capital_activity": out["ce_capital_flow"].abs() + out["pe_capital_flow"].abs(),
        "oi_change_activity": out["ce_oi_chg"].abs() + out["pe_oi_chg"].abs(),
        "turnover_activity": out.get("ce_premium_turnover", 0) + out.get("pe_premium_turnover", 0),
        "gamma_activity": out["ce_gamma_exposure"] + out["pe_gamma_exposure"],
        "delta_activity": out["net_delta_exposure"].abs(),
        "writing_activity": ce_writing_oi.abs() + pe_writing_oi.abs(),
    })

    footprint_score = pd.Series(0.0, index=out.index)
    for component, weight in _WEIGHTS.items():
        pct_col = f"footprint_pct_{component}"
        # pct=True ranks 0..1 (100th percentile = loudest strike for this
        # component); ties get the average rank, so identical zero-activity
        # strikes (the common case away from ATM) all land at the same
        # low percentile rather than an arbitrary ordering among them.
        out[pct_col] = raw[component].rank(pct=True, method="average") * 100
        footprint_score = footprint_score + out[pct_col].fillna(0) * weight

    out["footprint_score"] = footprint_score.round(1)
    return out


def rank_footprint_strikes(footprint_df: pd.DataFrame, top_n: int = 8) -> list[dict]:
    """
    footprint_df: output of compute_footprint_score() above (must already
    have the footprint_score column).

    Returns the top_n strikes by footprint_score, each as
    {strike, footprintScore, dominantSide} — dominantSide ("CE"/"PE") is
    whichever leg's capital_flow magnitude is larger at that strike, a
    quick "who's driving this strike's score" read for the UI without
    exposing all six component columns inline.
    """
    if footprint_df is None or footprint_df.empty or "footprint_score" not in footprint_df.columns:
        return []

    top = footprint_df.sort_values("footprint_score", ascending=False).head(top_n)
    rows = []
    for _, r in top.iterrows():
        ce_flow = abs(r.get("ce_capital_flow", 0) or 0)
        pe_flow = abs(r.get("pe_capital_flow", 0) or 0)
        rows.append({
            "strike": float(r["strike"]),
            "footprintScore": float(r["footprint_score"]),
            "dominantSide": "CE" if ce_flow >= pe_flow else "PE",
        })
    return rows


def compute_capital_concentration(capital_df: pd.DataFrame, top_n: int = 5) -> dict:
    """
    Spec item #10 — top-N strikes by total_premium_locked (already a
    per-strike CE+PE combined column on compute_capital_metrics()'s
    output) and what share of the WHOLE VISIBLE CHAIN's capital they hold.

    "Whole visible chain" means whatever strike window capital_df was
    built from (the same ATM-range filter every other capital_metrics
    consumer already applies) — not literally every strike NSE lists,
    consistent with how the rest of this analytics layer reads the chain.
    """
    if capital_df is None or capital_df.empty or "total_premium_locked" not in capital_df.columns:
        return {"topStrikes": [], "topCapital": 0.0, "totalCapital": 0.0, "concentrationPct": 0.0}

    total_capital = float(capital_df["total_premium_locked"].sum(skipna=True))
    top = capital_df.sort_values("total_premium_locked", ascending=False).head(top_n)
    top_capital = float(top["total_premium_locked"].sum(skipna=True))

    top_strikes = [
        {"strike": float(r["strike"]), "capitalLocked": float(r["total_premium_locked"] or 0)}
        for _, r in top.iterrows()
    ]

    return {
        "topStrikes": top_strikes,
        "topCapital": top_capital,
        "totalCapital": total_capital,
        "concentrationPct": round(top_capital / total_capital * 100, 1) if total_capital > 0 else 0.0,
    }
