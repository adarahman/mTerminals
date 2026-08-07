"""
analytics/capital_futures_confirmation.py
--------------------------------------------
Phase B of the Institutional Positioning Analytics layer — spec items
#2 (Capital Flow vs Futures OI), #3 (Capital Confirmation), and
#4 (Futures-Options Divergence). All three are thin classification logic
on top of numbers already computed elsewhere:

  - net_capital_flow, capital_pcr  <- oi.capital_metrics.compute_chain_metrics()
  - fut_oi_chg / fut_oi_chg_pct    <- oi.futures_oi_tracker.FuturesOITracker
  - regime                        <- analytics.market_regime.classify_market_regime()
  - price_chg_pct                 <- engine.py's day_chg_pct (same input regime used)

No new data fetching, no new per-strike computation — matches the spec's
"reuse existing calculations wherever possible" guideline. This module
composes those four already-computed numbers three different ways rather
than re-deriving bullish/bearish from raw OI a third time.

SIGN CONVENTION: net_capital_flow > 0 means PE-side capital flow exceeds
CE-side (more premium x OI-change on puts than calls this session) — same
"heavier put positioning = bullish" convention engine.py's own
pcr_sentiment already uses for total_pcr (PCR > PCR_BULL threshold = ==
bullish). Both readings mean "the options market is leaning bullish", so
this module treats net_capital_flow > 0 as the options market's bullish
vote throughout.
"""

from __future__ import annotations

__all__ = [
    "classify_capital_vs_futures",
    "compute_capital_confirmation",
    "detect_futures_options_divergence",
]

_BULLISH_REGIMES = {"Long Build-up", "Short Covering"}
_BEARISH_REGIMES = {"Short Build-up", "Long Unwinding"}


# ── #2: Capital Flow vs Futures OI ─────────────────────────────────────────
_CAPITAL_VS_FUTURES_LABELS = {
    (True, True): (
        "Institutional Long Addition",
        "Options capital and futures OI both building in the same direction — fresh institutional conviction.",
    ),
    (True, False): (
        "Hedging / Short Covering",
        "Options capital building while futures OI unwinds — reads as hedging flow or futures shorts covering, not fresh directional conviction.",
    ),
    (False, True): (
        "Futures Dominant Positioning",
        "Futures OI building while options capital flow is flat/negative — the futures desk is leading, options market hasn't confirmed yet.",
    ),
    (False, False): (
        "Risk Reduction",
        "Both options capital and futures OI unwinding — net de-risking across both markets.",
    ),
}


def classify_capital_vs_futures(net_capital_flow: float, fut_oi_chg: float) -> dict:
    """
    net_capital_flow: oi.capital_metrics.compute_chain_metrics()["net_capital_flow"]
    fut_oi_chg:        oi.futures_oi_tracker's session ΔFutOI (absolute, not %)

    "Capital up"/"Futures OI up" here means the raw sign of the change, not
    magnitude vs a threshold — unlike market_regime's flat-deadband, a
    single-rupee net_capital_flow still reads as "up". If that proves too
    twitchy in practice once this is live against real ticks, add the same
    kind of deadband market_regime.py uses.
    """
    capital_up = net_capital_flow > 0
    futures_up = fut_oi_chg > 0
    label, description = _CAPITAL_VS_FUTURES_LABELS[(capital_up, futures_up)]
    return {
        "label": label,
        "description": description,
        "capitalUp": capital_up,
        "futuresOiUp": futures_up,
        "netCapitalFlow": net_capital_flow,
        "futOiChg": fut_oi_chg,
    }


# ── #3: Capital Confirmation ────────────────────────────────────────────────
def compute_capital_confirmation(net_capital_flow: float, regime: str,
                                  price_chg_pct: float,
                                  volume_ratio: "float | None" = None) -> dict:
    """
    Three independent directional votes (-1 bearish / 0 neutral / +1 bullish):
      - capital_vote  from net_capital_flow's sign (options market)
      - regime_vote   from market_regime's regime label (futures market,
                       already Price x Futures OI — see market_regime.py)
      - price_vote    from price_chg_pct's sign (spot itself)

    volume_ratio (optional): total chain volume / total chain OI for this
    tick — NOT a directional vote (volume has no sign), it's a conviction
    multiplier: elevated turnover behind an agreeing move upgrades "Weak
    Confirmation" to "Confirmed"; if omitted, confirmation is graded on
    vote agreement alone. There's no session-average volume baseline yet
    to compare against, so >0.12 (empirical rule of thumb, not
    calibrated) stands in for "elevated" until one exists — see
    oi/futures_oi_tracker.py-style session tracking as the pattern to
    follow if this needs a real baseline later.

    Output "confirmation": Confirmed Bullish | Confirmed Bearish |
    Weak Confirmation | Divergence
    """
    capital_vote = 1 if net_capital_flow > 0 else -1 if net_capital_flow < 0 else 0
    regime_vote = 1 if regime in _BULLISH_REGIMES else -1 if regime in _BEARISH_REGIMES else 0
    price_vote = 1 if price_chg_pct > 0 else -1 if price_chg_pct < 0 else 0

    votes = [capital_vote, regime_vote, price_vote]
    bullish_votes = votes.count(1)
    bearish_votes = votes.count(-1)
    volume_elevated = volume_ratio is not None and volume_ratio > 0.12

    if bullish_votes and bearish_votes:
        confirmation = "Divergence"
    elif bullish_votes >= 2:
        confirmation = "Confirmed Bullish" if (bullish_votes == 3 or volume_elevated) else "Weak Confirmation"
    elif bearish_votes >= 2:
        confirmation = "Confirmed Bearish" if (bearish_votes == 3 or volume_elevated) else "Weak Confirmation"
    else:
        confirmation = "Weak Confirmation"

    return {
        "confirmation": confirmation,
        "capitalVote": capital_vote,
        "regimeVote": regime_vote,
        "priceVote": price_vote,
        "volumeElevated": volume_elevated,
        "volumeRatio": round(volume_ratio, 4) if volume_ratio is not None else None,
    }


# ── #4: Futures-Options Divergence ─────────────────────────────────────────
def detect_futures_options_divergence(regime: str, net_capital_flow: float) -> dict:
    """
    Futures side: bullish if regime in {Long Build-up, Short Covering},
    bearish if in {Short Build-up, Long Unwinding}, else neutral
    (Indeterminate regime — no divergence call possible yet).

    Options side: bullish if net_capital_flow > 0 (see module docstring's
    sign convention), bearish if < 0, else neutral.

    A same-direction read is "Aligned" (not a divergence); a genuine
    opposite-direction read flags the trap/hedge pattern the spec
    describes. Two neutral or one-neutral-one-directional reads are
    "Insufficient Data" — not confidently either aligned or diverged.
    """
    futures_side = "Bullish" if regime in _BULLISH_REGIMES \
        else "Bearish" if regime in _BEARISH_REGIMES else "Neutral"
    options_side = "Bullish" if net_capital_flow > 0 \
        else "Bearish" if net_capital_flow < 0 else "Neutral"

    if futures_side == "Neutral" or options_side == "Neutral":
        status, description = "Insufficient Data", "Regime or capital flow not established yet."
    elif futures_side == options_side:
        status, description = "Aligned", f"Futures and options positioning agree — both {futures_side.lower()}."
    else:
        status = f"{futures_side} Futures + {options_side} Options"
        description = (
            "Futures market and options market are pricing this in opposite directions — "
            "watch for a trap (one side is wrong) or genuine hedging flow, not fresh conviction."
        )

    return {
        "status": status,
        "description": description,
        "futuresSide": futures_side,
        "optionsSide": options_side,
        "isDivergent": futures_side != "Neutral" and options_side != "Neutral" and futures_side != options_side,
    }
