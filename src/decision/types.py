"""Decision thresholds and compatibility exports.

Extracted from decision_engine.py so signal_builder.py, confidence.py, and
strategy_selection.py can import T / ActiveSignal / DecisionResult without
reaching into decision_engine.py itself. Output contracts now live in
``core.domain`` while these imports remain compatible.
"""

from __future__ import annotations
from core.domain import ActiveSignal, DecisionResult


# ── Thresholds ────────────────────────────────────────────────────────────────
# Tune per symbol: BANKNIFTY → MP_GRAVITY ~150, OI_VEL_STRONG ~0.20

class T:
    # PCR: PE_OI / CE_OI
    # HIGH PCR (>1.2) → heavy put writing → BULLISH
    # LOW  PCR (<0.8) → heavy call writing → BEARISH
    PCR_BULL_EXTREME  = 1.40   # very strong bullish
    PCR_BULL          = 1.20
    PCR_BEAR          = 0.80
    PCR_BEAR_EXTREME  = 0.60   # very strong bearish
    PCR_NEUTRAL_HI    = 1.10
    PCR_NEUTRAL_LO    = 0.90

    VIX_LOW    = 13.0          # sell-premium regime
    VIX_NORMAL = 18.0
    VIX_HIGH   = 22.0          # reduce short gamma
    VIX_PANIC  = 26.0          # long vol only

    MP_GRAVITY      = 80       # pts; strong pull below this
    MP_PIN          = 30       # pts; pin-zone
    OI_VEL_MILD     = 0.08
    OI_VEL_MODERATE = 0.15
    OI_VEL_STRONG   = 0.25

    IV_LOW     = 30            # iv_rank
    IV_MID     = 50
    IV_HIGH    = 65
    IV_EXTREME = 80

    # Below this confidence (or on WAIT/conflict), a strategy is still
    # computed and shown (so the person can see what the engine WOULD do),
    # but should not be presented as execute-ready.
    CONFIDENCE_EXECUTE_MIN = 40

    # IV crush: a fast VIX drop from its recent peak while positions are
    # still open (buildup, not unwinding) — the classic post-event trap
    # where Vega losses eat a correctly-directioned Delta gain.
    IV_CRUSH_WINDOW_SECONDS = 300   # look back this far for the recent peak
    IV_CRUSH_PCT            = 8.0   # % drop from peak that counts as a crush
    IV_CRUSH_MAX_AGE_SECONDS = 900  # prune history older than this


__all__ = ["T", "ActiveSignal", "DecisionResult"]
