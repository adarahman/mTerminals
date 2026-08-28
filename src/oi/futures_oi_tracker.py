"""
oi/futures_oi_tracker.py
-------------------------
Session-baseline tracker for FUTURES open interest, so callers can get a
true day-over-day ΔFutOI (not a one-tick delta) the same way
tick_pipeline.TickAggregator._session_open_oi does for option-leg
OI. This is the input Market Regime (Price vs Futures OI) needs — engine.py
previously only had a single point-in-time futures OI snapshot per tick,
with nothing to diff it against.

Why this isn't just another field on TickAggregator: futures aren't part
of the option-chain SmartAPI WS subscription (see tick_pipeline.py's
docstring — that class's token_meta is built from get_atm_chain()'s CE/PE/
INDEX rows only). Futures OI arrives via the separate REST poll
(smartapi_pipeline_adapter.fetch_futures_wide(), called once per pipeline
tick from option_chain_json.py's main()), which is single-threaded under
server/app.py's _PIPELINE_LOCK — so this tracker doesn't need
TickAggregator's threading.Lock rigor, but keeps one anyway since
reset_session() is called from MarketEngineCycle's asyncio task, a different
call path than the update() calls happening inside the pipeline thread.

KEYED BY CONTRACT SYMBOL, not underlying: NIFTY/BANKNIFTY futures roll to
a new monthly contract periodically. A new contract symbol has no baseline
yet — update() bootstraps on that contract's first sighting (fut_oi_chg
reads 0 for that first tick, then accumulates correctly from there), the
same self-correcting "first tick establishes it" fallback
TickAggregator.on_tick() uses for un-seeded option tokens. There is no
NSE-style authoritative changeinOpenInterest to seed this against up
front (unlike seed_session_baseline() for options) — SmartAPI's futures
quote has no such field — so the first-tick bootstrap IS the baseline
here, not just a stopgap until a better seed arrives.
"""

from __future__ import annotations

import threading

__all__ = ["FuturesOITracker", "get_tracker"]


class FuturesOITracker:
    def __init__(self):
        self._session_oi = {}   # contract_symbol -> baseline OI, fixed for the session
        self._lock = threading.Lock()

    def reset_session(self):
        """Call at actual trading-day rollover (same hook as
        TickAggregator.reset_session() — the server's daily market scheduler),
        NOT on an ordinary contract rollover
        within the same day (a rollover mid-day is handled automatically:
        the new contract symbol just has no baseline yet, see class
        docstring)."""
        with self._lock:
            self._session_oi.clear()

    def update(self, contract: str, oi: "float | int | None") -> dict:
        """Call once per pipeline tick with the current futures contract's
        symbol and its live OI (df_fut['OI'].iloc[0] / fetch_futures_wide()'s
        'OI' field).

        Returns {"fut_oi": float, "fut_oi_chg": float, "fut_oi_chg_pct": float}.
        All three are 0.0 if contract is falsy or oi is None/NaN — callers
        (engine.py) should treat that as "no data yet", not "flat", when
        deciding regime confidence.
        """
        if not contract or oi is None:
            return {"fut_oi": 0.0, "fut_oi_chg": 0.0, "fut_oi_chg_pct": 0.0}
        try:
            oi = float(oi)
        except (TypeError, ValueError):
            return {"fut_oi": 0.0, "fut_oi_chg": 0.0, "fut_oi_chg_pct": 0.0}
        if oi != oi:  # NaN check without importing math/pandas here
            return {"fut_oi": 0.0, "fut_oi_chg": 0.0, "fut_oi_chg_pct": 0.0}

        with self._lock:
            baseline = self._session_oi.get(contract)
            if baseline is None:
                self._session_oi[contract] = oi
                baseline = oi

        fut_oi_chg = oi - baseline
        fut_oi_chg_pct = (fut_oi_chg / baseline * 100.0) if baseline > 0 else 0.0
        return {"fut_oi": oi, "fut_oi_chg": fut_oi_chg, "fut_oi_chg_pct": fut_oi_chg_pct}


# Singleton — one live futures contract in flight per process at a time
# (mirrors the single module-level _smartapi_aggregator instance pattern
# in server/app.py), so engine.py and server/app.py's day-rollover
# hook share the same tracker without needing it threaded through every
# call signature.
_TRACKER = FuturesOITracker()


def get_tracker() -> FuturesOITracker:
    return _TRACKER
