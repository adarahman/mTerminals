"""
analytics/market_regime.py
----------------------------
Highest-priority item of the Institutional Positioning Analytics layer:
classify the current market regime from Price change x Futures OI change.

    Price up   + Futures OI up   -> Long Build-up     (fresh longs)
    Price down + Futures OI up   -> Short Build-up     (fresh shorts)
    Price up   + Futures OI down -> Short Covering     (shorts exiting)
    Price down + Futures OI down -> Long Unwinding     (longs exiting)

This replaces engine.py's old `fut_signal`, which only looked at futures
basis sign (fut_ltp - spot) and only ever produced "Long Buildup" /
"Short Buildup" — it never actually looked at futures OI at all, despite
the name. fut_signal is left in place for existing callers (frontend
still reads ctx_dict["fut_signal"]); market_regime is the new, correctly-
named replacement metric feeding the Market Regime executive card.

Threshold-based, not statistically fit — see NOTE ON THRESHOLDS below for
why, and where to revisit if regime calls prove noisy in practice.
"""

from __future__ import annotations

__all__ = ["classify_market_regime"]

# NOTE ON THRESHOLDS: a real trading day sees plenty of sub-0.05% chop in
# both spot and futures OI that isn't a genuine regime signal — without a
# deadband, "Price up 0.001% + OI up 0.001%" would confidently call "Long
# Build-up" on pure noise. These are deliberately conservative starting
# points (not backtested/calibrated) so the regime label only flips on a
# move actually worth naming; tighten/loosen once real intraday data shows
# how noisy fut_oi_chg_pct actually is tick-to-tick.
_PRICE_FLAT_PCT = 0.03      # |price_chg_pct| below this = "flat", not up/down
_OI_FLAT_PCT = 0.05         # |fut_oi_chg_pct| below this = "flat", not up/down

_REGIME_LABELS = {
    (True, True): "Long Build-up",
    (False, True): "Short Build-up",
    (True, False): "Short Covering",
    (False, False): "Long Unwinding",
}

_REGIME_DESCRIPTIONS = {
    "Long Build-up": "Price rising with futures OI rising — fresh long positions being added.",
    "Short Build-up": "Price falling with futures OI rising — fresh short positions being added.",
    "Short Covering": "Price rising with futures OI falling — shorts exiting, not new buying.",
    "Long Unwinding": "Price falling with futures OI falling — longs exiting, not fresh selling.",
}


def classify_market_regime(price_chg_pct: float, fut_oi_chg_pct: float,
                            has_oi_data: bool = True) -> dict:
    """
    price_chg_pct:   spot day-change %, e.g. engine.py's day_chg_pct.
    fut_oi_chg_pct:  session futures-OI change %, from
                      oi.futures_oi_tracker.FuturesOITracker.update().
    has_oi_data:     False when the futures OI tracker had nothing to
                      diff against yet (contract's first tick of the
                      session/day, or df_fut unavailable) — the regime
                      call in that case is not meaningful (fut_oi_chg_pct
                      reads exactly 0.0 by construction on that tick, which
                      would otherwise be indistinguishable from a
                      genuinely flat OI day) and confidence is forced to 0.

    Returns:
      {
        "regime": one of the four labels above, or "Indeterminate",
        "confidence": 0-100 int,
        "description": one-line explanation,
        "price_chg_pct": float,
        "fut_oi_chg_pct": float,
      }
    """
    result = {
        "regime": "Indeterminate",
        "confidence": 0,
        "description": "Not enough data yet to classify the regime.",
        "price_chg_pct": round(price_chg_pct, 3),
        "fut_oi_chg_pct": round(fut_oi_chg_pct, 3),
    }

    if not has_oi_data:
        return result

    price_flat = abs(price_chg_pct) < _PRICE_FLAT_PCT
    oi_flat = abs(fut_oi_chg_pct) < _OI_FLAT_PCT
    if price_flat or oi_flat:
        result["description"] = (
            "Price and/or futures OI move too small to call a regime "
            f"(price {price_chg_pct:+.2f}%, futures OI {fut_oi_chg_pct:+.2f}%)."
        )
        return result

    price_up = price_chg_pct > 0
    oi_up = fut_oi_chg_pct > 0
    regime = _REGIME_LABELS[(price_up, oi_up)]

    # Confidence: how far each leg sits past its own flat-deadband,
    # scaled so a strong day (e.g. price +1.0%, OI +3%) saturates near
    # 100 rather than the two legs fighting each other in a product.
    # Deliberately simple (average of two capped ratios) — no claim this
    # is a calibrated probability, just a relative "how clear-cut is
    # this call" indicator for the UI's confidence bar.
    price_strength = min(abs(price_chg_pct) / (_PRICE_FLAT_PCT * 10), 1.0)
    oi_strength = min(abs(fut_oi_chg_pct) / (_OI_FLAT_PCT * 10), 1.0)
    confidence = round((price_strength + oi_strength) / 2 * 100)

    result.update({
        "regime": regime,
        "confidence": confidence,
        "description": _REGIME_DESCRIPTIONS[regime],
    })
    return result
