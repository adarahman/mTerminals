"""
analytics/smart_money_summary.py
-----------------------------------
One executive card rolling up Market Regime + Capital Flow + Futures OI
into a single Smart Money Bias + Confidence — item #12 of the
Institutional Positioning Analytics spec.

Deliberately does NOT include an Institutional Footprint Score (#5) yet —
that's a separate composite (Capital Flow + Futures OI + OI Change +
Gamma + Delta + Volume + Call/Put Writing) not yet built; this summary
reads only market_regime (analytics/market_regime.py) and the existing
capital_summary rollup (oi.capital_metrics.compute_chain_metrics()) that
mTerminals_json.py already computes for the Executive card. Add
footprintScore as a field here once that module exists rather than
building a second, competing composite in the meantime.

Inputs are all already-computed dicts — this module does no data
fetching or per-strike computation of its own, matching the "reuse
existing calculations, avoid duplicate computations" implementation
guideline in the spec.
"""

from __future__ import annotations

__all__ = ["compute_smart_money_summary"]

# Regimes that mean money is net constructive on price (longs building or
# shorts leaving) vs net destructive (shorts building or longs leaving).
_BULLISH_REGIMES = {"Long Build-up", "Short Covering"}
_BEARISH_REGIMES = {"Short Build-up", "Long Unwinding"}


def compute_smart_money_summary(market_regime: dict, capital_summary: dict,
                                 fut_oi_chg_pct: float) -> dict:
    """
    market_regime:    output of analytics.market_regime.classify_market_regime()
    capital_summary:  output of oi.capital_metrics.compute_chain_metrics()
                       ({} is fine — treated as "no capital data yet")
    fut_oi_chg_pct:    session futures OI change %, same value market_regime
                       was built from (passed separately since capital_summary
                       doesn't carry it)

    Returns:
      {
        "bias": "Bullish" | "Bearish" | "Neutral",
        "confidence": 0-100 int,
        "regime": str (mirrors market_regime["regime"]),
        "netCapitalFlow": float,
        "capitalConfirms": bool | None (None = no capital data to check against),
        "futOiChgPct": float,
        "summary": one-line human-readable string for the card,
      }
    """
    regime = market_regime.get("regime", "Indeterminate")
    regime_confidence = market_regime.get("confidence", 0)
    net_capital_flow = capital_summary.get("net_capital_flow") if capital_summary else None

    if regime in _BULLISH_REGIMES:
        bias = "Bullish"
    elif regime in _BEARISH_REGIMES:
        bias = "Bearish"
    else:
        bias = "Neutral"

    capital_confirms = None
    confidence = regime_confidence
    if net_capital_flow is not None and bias != "Neutral":
        capital_agrees = (bias == "Bullish" and net_capital_flow > 0) or \
                          (bias == "Bearish" and net_capital_flow < 0)
        capital_confirms = bool(capital_agrees)
        # Capital flow confirming the regime call is corroboration from an
        # independent signal (option premium positioning, not futures) —
        # bump confidence toward the call being real rather than noise.
        # Diverging pulls it back down toward "mixed signals, be careful"
        # rather than flatly overriding the regime call either way.
        confidence = min(100, regime_confidence + 15) if capital_confirms \
            else max(0, regime_confidence - 20)

    if bias == "Neutral":
        summary = market_regime.get("description", "No clear regime yet.")
    else:
        confirm_txt = (
            "capital flow confirms" if capital_confirms
            else "capital flow diverges" if capital_confirms is False
            else "capital flow data pending"
        )
        summary = f"{bias} — {regime} ({confirm_txt})."

    return {
        "bias": bias,
        "confidence": confidence,
        "regime": regime,
        "netCapitalFlow": net_capital_flow if net_capital_flow is not None else 0.0,
        "capitalConfirms": capital_confirms,
        "futOiChgPct": round(fut_oi_chg_pct, 3),
        "summary": summary,
    }
