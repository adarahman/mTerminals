"""
oi/capital_metrics.py
----------------------
Capital-weighted per-strike metrics, computed once off the already-built
`master` table (oi_analysis.build_master_table_nse output) so every
consumer (Executive Card, Smart Money panel, heatmaps, Decision Bar)
reads the same numbers instead of re-deriving them independently.

All OI figures on `master` (ce_oi, pe_oi, ce_oi_chg, pe_oi_chg) are already
lot-scaled to underlying quantity terms — see build_master_table_nse's
docstring. These functions do NOT multiply by lot_size again. ce_volume/
pe_volume are raw contract counts (NOT lot-scaled), so premium_turnover
below multiplies by lot_size explicitly.

IMPORTANT — capital_flow here uses ce_oi_chg/pe_oi_chg (NSE's day-session
ChgOI vs previous close), not the intraday 5/15/30-min vel_df deltas.
vel_df's ceDOI/peDOI would be the more responsive "new money entering"
input, but decision/signal_builder.py's score_oi_velocity()/_detect_traps()
currently read vel_df via row.get("Strike")/row.get("CE_OI_Delta") — column
names that don't exist on oi_analysis.get_oi_velocity()'s actual output
(window/strike/ceNow/ceDOI/ceLTP/peNow/peDOI/peLTP/signal/actual_age_min).
Those calls silently default to 0 for every row, so the OI-velocity
contribution to decision scoring is currently always exactly zero. An
intraday capital-flow variant should read strike/ceDOI/peDOI/ceLTP/peLTP
directly off vel_df once that mismatch is fixed — not built on top of it
here.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["compute_capital_metrics", "compute_chain_metrics", "capital_percentages"]

_REQUIRED_COLS = {
    "strike", "ce_oi", "pe_oi", "ce_ltp", "pe_ltp",
    "ce_oi_chg", "pe_oi_chg", "ce_volume", "pe_volume",
    "ce_iv", "pe_iv", "ce_delta", "pe_delta", "ce_gamma", "pe_gamma",
}

# Every column compute_capital_metrics() adds — cast to float64 explicitly
# at the end rather than trusting pandas' inferred dtype. Most of these end
# up float64 anyway (multiplying by ce_ltp/spot/delta, all floats, upcasts
# automatically), but ce_notional_exposure/ce_notional_exposure_spot are
# oi x strike / oi x spot — if both operands happen to be int64 (e.g. a
# caller passes an integer spot), the result stays int64. Not a realistic
# overflow risk at real OI/strike magnitudes (int64 tops out at ~9.2e18;
# real chain-wide notional is ~1e13), but explicit float64 keeps every
# derived column's dtype consistent regardless of what master's own
# columns happen to be, and consistent dtypes matter more once this feeds
# JSON serialization / gets summed alongside other float columns downstream.
_DERIVED_COLS = [
    "ce_notional_exposure", "pe_notional_exposure",
    "ce_notional_exposure_spot", "pe_notional_exposure_spot",
    "ce_premium_locked", "pe_premium_locked",
    "total_premium_locked", "net_premium_locked",
    "ce_capital_flow", "pe_capital_flow", "net_capital_flow",
    "ce_premium_turnover", "pe_premium_turnover",
    "ce_delta_exposure", "pe_delta_exposure", "net_delta_exposure",
    "ce_gamma_exposure", "pe_gamma_exposure",
]


def compute_capital_metrics(master: pd.DataFrame, spot: float, lot_size: int) -> pd.DataFrame:
    """
    Returns a NEW DataFrame (does not mutate `master`) — a copy of `master`
    with these columns added, one row per strike:

      ce_notional_exposure / pe_notional_exposure        OI x strike (contract
                                                            notional — standard
                                                            definition, fixed
                                                            per contract)
      ce_notional_exposure_spot / pe_notional_exposure_spot
                                                           OI x spot (current
                                                            underlying exposure
                                                            — moves with spot,
                                                            unlike the strike-
                                                            based figure above)
      ce_premium_locked    / pe_premium_locked            OI x LTP
      ce_capital_flow      / pe_capital_flow              ChgOI x LTP (day-
                                                            session, NOT
                                                            intraday — see
                                                            module docstring)
      ce_premium_turnover  / pe_premium_turnover          Volume x lot_size x LTP
      ce_delta_exposure    / pe_delta_exposure            OI x Delta x Spot
      ce_gamma_exposure    / pe_gamma_exposure            OI x Gamma x Spot^2

      total_premium_locked                                ce_premium_locked +
                                                            pe_premium_locked
                                                            (denominator for a
                                                            combined-chain %,
                                                            as opposed to a
                                                            CE-only or PE-only
                                                            % via
                                                            capital_percentages())
      net_premium_locked   = pe_premium_locked - ce_premium_locked
      net_capital_flow     = pe_capital_flow - ce_capital_flow
      net_delta_exposure   = ce_delta_exposure + pe_delta_exposure  (a SUM,
                                                            not a difference —
                                                            pe_delta is already
                                                            signed negative
                                                            coming out of
                                                            oi_analysis.
                                                            calculate_greeks_
                                                            vectorized, so
                                                            summing the two
                                                            legs nets them
                                                            correctly; do not
                                                            feed this an
                                                            unsigned PE delta)

    NaN HANDLING: if a strike's ce_ltp/ce_delta/ce_gamma (etc.) is NaN in
    `master` — e.g. an illiquid strike with no live IV yet — the derived
    metric for that strike is deliberately left NaN too, rather than
    coerced to 0. A 0 would misleadingly claim "no exposure here" when the
    truth is "we don't know". compute_chain_metrics() below sums with
    skipna=True, so a handful of NaN strikes don't poison the chain-wide
    total; a per-strike consumer (heatmap, table) should decide for itself
    whether to display NaN as blank/"—" or as 0, rather than that decision
    being baked in here.

    All added columns are explicitly cast to float64 before return — see
    _DERIVED_COLS' comment for why.

    Raises ValueError if `master` is missing any of the columns this reads
    (fail loudly here rather than silently emitting a column of zeros —
    exactly the failure mode that caused the earlier netGEX bug and the
    vel_df key-mismatch flagged above).
    """
    missing = _REQUIRED_COLS - set(master.columns)
    if missing:
        raise ValueError(f"compute_capital_metrics: master is missing columns {sorted(missing)}")

    out = master.copy()
    spot_sq = spot * spot  # computed once, reused for both legs below

    out["ce_notional_exposure"] = out["ce_oi"] * out["strike"]
    out["pe_notional_exposure"] = out["pe_oi"] * out["strike"]
    out["ce_notional_exposure_spot"] = out["ce_oi"] * spot
    out["pe_notional_exposure_spot"] = out["pe_oi"] * spot

    out["ce_premium_locked"] = out["ce_oi"] * out["ce_ltp"]
    out["pe_premium_locked"] = out["pe_oi"] * out["pe_ltp"]
    out["total_premium_locked"] = out["ce_premium_locked"] + out["pe_premium_locked"]
    out["net_premium_locked"] = out["pe_premium_locked"] - out["ce_premium_locked"]

    out["ce_capital_flow"] = out["ce_oi_chg"] * out["ce_ltp"]
    out["pe_capital_flow"] = out["pe_oi_chg"] * out["pe_ltp"]
    out["net_capital_flow"] = out["pe_capital_flow"] - out["ce_capital_flow"]

    out["ce_premium_turnover"] = out["ce_volume"] * lot_size * out["ce_ltp"]
    out["pe_premium_turnover"] = out["pe_volume"] * lot_size * out["pe_ltp"]

    # NOTE ON SIGN: pe_delta is already negative (see calculate_greeks_
    # vectorized in oi_analysis.py — PE delta = exp_qt*(N(d1)-1), which is
    # < 0 for any normal input). Do not `abs()` or re-sign it here or in a
    # caller — that would silently break net_delta_exposure below.
    # Greeks are only verified when the corresponding leg has positive IV
    # and OI. build_master_table_nse uses numeric zero as its internal
    # calculation sentinel; do not let that become a displayed claim of
    # zero Stage-2 exposure. Nullable exposure is the product contract.
    ce_greeks_valid = (out["ce_iv"] > 0) & (out["ce_oi"] > 0)
    pe_greeks_valid = (out["pe_iv"] > 0) & (out["pe_oi"] > 0)
    out["ce_delta_exposure"] = (out["ce_oi"] * out["ce_delta"] * spot).where(ce_greeks_valid)
    out["pe_delta_exposure"] = (out["pe_oi"] * out["pe_delta"] * spot).where(pe_greeks_valid)
    out["net_delta_exposure"] = out["ce_delta_exposure"] + out["pe_delta_exposure"]

    out["ce_gamma_exposure"] = (out["ce_oi"] * out["ce_gamma"] * spot_sq).where(ce_greeks_valid)
    out["pe_gamma_exposure"] = (out["pe_oi"] * out["pe_gamma"] * spot_sq).where(pe_greeks_valid)

    out[_DERIVED_COLS] = out[_DERIVED_COLS].astype("float64")

    return out


def compute_chain_metrics(capital_df: pd.DataFrame) -> dict:
    """
    Chain-wide rollup of compute_capital_metrics()'s per-strike columns —
    the "Executive Card" numbers (a single net figure) rather than a
    400-strike table. Takes the OUTPUT of compute_capital_metrics(), not
    raw master.

    Stage-1 sums use skipna=True. Stage-2 Greek exposure is stricter: if
    any visible strike lacks verified Greeks, the chain exposure is None
    rather than a misleading partial total or manufactured zero.

    Deliberately does NOT include a smart-money/institutional composite
    score here — those weights are uncalibrated (see the conversation this
    was built in); this stays to straightforward sums/ratios of numbers
    already computed above.

    Returns a plain dict (not a dataclass — no per-tick allocation pattern
    to optimize here; this is one dict per tick, not one per strike).
    """
    total_ce_locked = float(capital_df["ce_premium_locked"].sum(skipna=True))
    total_pe_locked = float(capital_df["pe_premium_locked"].sum(skipna=True))
    total_ce_flow = float(capital_df["ce_capital_flow"].sum(skipna=True))
    total_pe_flow = float(capital_df["pe_capital_flow"].sum(skipna=True))
    def complete_stage2_sum(column):
        series = capital_df[column]
        if series.empty or series.isna().any():
            return None
        return float(series.sum())

    total_ce_gamma_exp = complete_stage2_sum("ce_gamma_exposure")
    total_pe_gamma_exp = complete_stage2_sum("pe_gamma_exposure")
    net_delta_exp = complete_stage2_sum("net_delta_exposure")

    # Capital-weighted "wall" — the strike holding the most premium, as
    # opposed to ce_wall/pe_wall elsewhere (engine.py), which is the
    # highest-raw-OI strike. These can point at different strikes: a
    # strike with huge OI in cheap far-OTM premium can lose the capital
    # wall to a smaller-OI, expensive near-ATM strike — exactly the
    # "raw OI misleads" case this whole capital-metrics layer exists for.
    ce_capital_wall_strike = (
        float(capital_df.loc[capital_df["ce_premium_locked"].idxmax(), "strike"])
        if capital_df["ce_premium_locked"].notna().any() else None
    )
    pe_capital_wall_strike = (
        float(capital_df.loc[capital_df["pe_premium_locked"].idxmax(), "strike"])
        if capital_df["pe_premium_locked"].notna().any() else None
    )

    return {
        "total_ce_premium_locked": total_ce_locked,
        "total_pe_premium_locked": total_pe_locked,
        "net_premium_locked": total_pe_locked - total_ce_locked,
        "total_ce_capital_flow": total_ce_flow,
        "total_pe_capital_flow": total_pe_flow,
        "net_capital_flow": total_pe_flow - total_ce_flow,
        "total_ce_notional_exposure": float(capital_df["ce_notional_exposure"].sum(skipna=True)),
        "total_pe_notional_exposure": float(capital_df["pe_notional_exposure"].sum(skipna=True)),
        "total_ce_notional_exposure_spot": float(capital_df["ce_notional_exposure_spot"].sum(skipna=True)),
        "total_pe_notional_exposure_spot": float(capital_df["pe_notional_exposure_spot"].sum(skipna=True)),
        "total_ce_premium_turnover": float(capital_df["ce_premium_turnover"].sum(skipna=True)),
        "total_pe_premium_turnover": float(capital_df["pe_premium_turnover"].sum(skipna=True)),
        "net_delta_exposure": net_delta_exp,
        "total_ce_gamma_exposure": total_ce_gamma_exp,
        "total_pe_gamma_exposure": total_pe_gamma_exp,
        # Differencing convention (CE - PE), matching chain_metrics.py's
        # existing netGEX — NOT a sum. Unlike delta, gamma is positive-
        # magnitude on both legs, so "net" here means "which side's gamma
        # dominates", the same thing netGEX already answers via raw OI;
        # this is the capital-weighted analogue of that same question.
        "net_gamma_exposure": (
            total_ce_gamma_exp - total_pe_gamma_exp
            if total_ce_gamma_exp is not None and total_pe_gamma_exp is not None else None
        ),
        "ce_capital_wall_strike": ce_capital_wall_strike,
        "pe_capital_wall_strike": pe_capital_wall_strike,
        # Capital-weighted PCR — same PE/CE ratio idea as
        # chain_metrics.compute_total_pcr(), but weighted by premium
        # locked instead of raw OI count. Diverging from OI-PCR is a
        # signal in itself (e.g. huge PE open interest sitting in cheap,
        # far-OTM premium vs a smaller CE position concentrated ATM).
        "capital_pcr": round(total_pe_locked / total_ce_locked, 2) if total_ce_locked > 0 else 0.0,
    }


def capital_percentages(df: pd.DataFrame, value_col: str, pct_col: str | None = None) -> pd.DataFrame:
    """
    Adds a `{value_col}_pct` column (0-100 scale): each row's share of that
    column's chain-wide total. Returns a NEW DataFrame. Use this for
    heatmap concentration ("34% of premium capital sits at 25000") rather
    than displaying raw rupee figures.

    Call this once per side you want a % for — e.g.
        capital_percentages(df, "ce_premium_locked")   # % of CE capital only
        capital_percentages(df, "pe_premium_locked")   # % of PE capital only
        capital_percentages(df, "total_premium_locked") # % of whole chain
    A strike can be a large share of the whole chain while a small share of
    its own side (or vice versa) — report whichever denominator the panel
    actually means, don't default to one silently.
    """
    pct_col = pct_col or f"{value_col}_pct"
    out = df.copy()
    total = out[value_col].sum()
    out[pct_col] = (out[value_col] / total * 100.0) if total > 0 else 0.0
    return out
