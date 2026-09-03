"""
backtest/replay.py
--------------------
Replays captured decision history (backtest/snapshot_logger.py) through
the REAL decision.auto_executor.AutoExecutor — not a reimplementation of
its gating logic — so a passing backtest actually says something about
auto_executor.py's production behavior, not about a parallel copy of it
that could quietly drift out of sync.

What this validates:
  - AutoExecutor.evaluate()'s gates (confidence floor, conflict flag,
    cooldown, per-symbol daily cap, WAIT/multi-leg exclusion) against
    real historical decision sequences, not synthetic ones.
  - Optionally, risk.account_guard.LiveAccountRiskGuard's trip logic
    (daily loss limit, drawdown streak) against the SIMULATED P&L this
    replay produces — pass `use_account_guard=True` to wire in a real
    LiveAccountRiskGuard instance backed by a throwaway SQLite file, fed
    with update_pnl() after every simulated exit exactly like
    server/app.py's live path does after every real fill.

What this does NOT validate: decision_engine.py's own scoring logic
(pcr_score/bias_score/etc.) — the decision snapshots are taken AS
DECIDED, not recomputed from raw chain data. See
backtest/snapshot_logger.py's module docstring for why.

Fill-price model (v1, deliberately simple — flag for review, not a
"can't be wrong" claim):
  - ENTRY fills at the suggested strike's LTP on the SAME tick the
    signal cleared (i.e. this replay assumes the algo's own tick is
    fast enough to fill at that tick's quoted price — optimistic vs. a
    real network+broker round-trip, same caveat any tick-level backtest
    without a true order book has).
  - EXIT triggers on either of two conditions, whichever comes first:
      (a) a later tick where AutoExecutor.evaluate() would clear an
          OPPOSITE-direction single-leg action for the same symbol
          (the "engine changed its mind" exit) — filled at that
          strike's LTP on that tick.
      (b) the trading day changes since entry (no positions are held
          overnight in this v1 model) — filled at the LAST LTP
          observed for that instrument/strike before the day boundary.
    If no LTP data exists at all for the held instrument, the position
    is marked UNPRICED rather than fabricating an exit price — refuse
    rather than guess, matching lot_sizes.py / account_guard.py's
    posture elsewhere in this codebase.
  - Any position still open when the data runs out is force-closed at
    the last observed LTP and flagged exit_reason="data_exhausted".

These are v1 defaults, not the only reasonable model — the missing
piece if you want tighter fidelity is a real target/stop-loss exit
rule pulled from strategy_selection.py's suggested_strategy output
rather than "hold until the signal flips."
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

import pandas as pd

from backtest.snapshot_logger import load_decision_snapshots
from decision.auto_executor import AutoExecutor
from oi.oi_analysis import JSON_HISTORY_LOG_PATH, VELOCITY_RETENTION_MINUTES

_OPPOSITE_SIDE = {"BUY": "SELL", "SELL": "BUY"}
_ACTION_INSTRUMENT_SIDE = {
    "BUY_CE": ("CE", "BUY"), "BUY_PE": ("PE", "BUY"),
    "SELL_CE": ("CE", "SELL"), "SELL_PE": ("PE", "SELL"),
}

# NSE cash/derivatives trading session. Snapshot/LTP logs have been
# observed to contain ticks well outside this window (server left
# running past close, dev/test sessions, etc.) — those are NOT real
# market prints and must never be used as a day-boundary settlement
# price, or an after-hours artifact silently becomes the "close" price
# for a position that should have squared off at the real 15:30 close.
MARKET_OPEN_TIME = "09:15:00"
MARKET_CLOSE_TIME = "15:30:00"
MARKET_OPEN_OFFSET = timedelta(hours=9, minutes=15)
MARKET_CLOSE_OFFSET = timedelta(hours=15, minutes=30)
MAX_FILL_LOOKAHEAD_SECONDS = 60


def _in_market_hours(ts: pd.Timestamp) -> bool:
    day = ts.normalize()
    return (day + MARKET_OPEN_OFFSET) <= ts <= (day + MARKET_CLOSE_OFFSET)


class _NoOpGuard:
    """Used when use_account_guard=False — never trips, so the backtest
    isolates AutoExecutor's own gates from account_guard's."""
    def is_tripped(self):
        return False, None
    def update_pnl(self, *_a, **_k):
        pass


@dataclass
class SimTrade:
    symbol: str
    expiry: str
    instrument_type: str
    side: str
    strike: int
    qty_lots: int
    lot_size: int
    entry_time: str
    entry_price: float
    # Decision-engine read AT ENTRY (not re-derived) — lets summary_by_
    # confidence_bucket() answer "does confidence actually predict P&L",
    # which the plain aggregate win_rate above cannot.
    confidence: Optional[int] = None
    bias_strength: Optional[str] = None
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None

    def close(self, exit_time: str, exit_price: float, reason: str):
        self.exit_time = exit_time
        self.exit_price = exit_price
        self.exit_reason = reason
        signed = (exit_price - self.entry_price) if self.side == "BUY" else (self.entry_price - exit_price)
        self.pnl = round(signed * self.lot_size * self.qty_lots, 2)


@dataclass
class BacktestResult:
    symbol: str
    trades: list = field(default_factory=list)
    unpriced_signals: int = 0  # cleared entries we couldn't fill — no LTP data at that strike/tick
    snapshot_count: int = 0
    data_start: Optional[str] = None
    data_end: Optional[str] = None
    requested_start: Optional[str] = None
    requested_end: Optional[str] = None

    @property
    def closed_trades(self):
        return [t for t in self.trades if t.pnl is not None]

    def summary(self) -> dict:
        closed = self.closed_trades
        if not closed:
            return {"num_trades": 0, "total_pnl": 0.0, "win_rate": None,
                    "avg_pnl": None, "max_drawdown": 0.0, "unpriced_signals": self.unpriced_signals}
        pnls = [t.pnl for t in closed]
        cum, peak, max_dd = 0.0, 0.0, 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        wins = sum(1 for p in pnls if p > 0)
        return {
            "num_trades": len(closed),
            "total_pnl": round(sum(pnls), 2),
            "win_rate": round(wins / len(closed), 3),
            "avg_pnl": round(sum(pnls) / len(closed), 2),
            "max_drawdown": round(max_dd, 2),
            "unpriced_signals": self.unpriced_signals,
        }

    def summary_by_confidence_bucket(
            self, edges: tuple = (40, 55, 70, 85, 101)) -> list[dict]:
        """Win rate / avg P&L broken out by the confidence value read AT
        ENTRY, so a bucket with a low win rate flags that
        compute_confidence()'s agreement-of-inputs score isn't tracking
        actual forward price behavior at that level — the plain aggregate
        summary() above cannot show this since it collapses every trade,
        low- and high-confidence alike, into one win rate.

        `edges` are bucket upper bounds, e.g. (40, 55, 70, 85, 101) makes
        buckets [40,55) [55,70) [70,85) [85,101). Trades with no recorded
        confidence (older snapshots predating this field) are skipped and
        counted separately so they don't silently distort a bucket.
        """
        closed = [t for t in self.closed_trades if t.confidence is not None]
        skipped_no_confidence = len(self.closed_trades) - len(closed)
        buckets = []
        lo = edges[0]
        for hi in edges[1:]:
            in_bucket = [t for t in closed if lo <= t.confidence < hi]
            if in_bucket:
                pnls = [t.pnl for t in in_bucket]
                wins = sum(1 for p in pnls if p > 0)
                buckets.append({
                    "range": f"{lo}-{hi - 1}",
                    "num_trades": len(in_bucket),
                    "win_rate": round(wins / len(in_bucket), 3),
                    "avg_pnl": round(sum(pnls) / len(in_bucket), 2),
                    "total_pnl": round(sum(pnls), 2),
                })
            else:
                buckets.append({
                    "range": f"{lo}-{hi - 1}", "num_trades": 0,
                    "win_rate": None, "avg_pnl": None, "total_pnl": 0.0,
                })
            lo = hi
        if skipped_no_confidence:
            buckets.append({
                "range": "no_confidence_recorded",
                "num_trades": skipped_no_confidence,
                "win_rate": None, "avg_pnl": None, "total_pnl": 0.0,
            })
        return buckets

    def metadata(self) -> dict:
        return {
            "snapshotCount": self.snapshot_count,
            "dataStart": self.data_start,
            "dataEnd": self.data_end,
            "requestedStart": self.requested_start,
            "requestedEnd": self.requested_end,
            "marketSession": f"{MARKET_OPEN_TIME}–{MARKET_CLOSE_TIME}",
            "entryFillModel": f"first logged LTP within {MAX_FILL_LOOKAHEAD_SECONDS}s after the decision tick",
            "exitFillModel": "opposite signal, session boundary, or final logged LTP",
            "transactionCostsIncluded": False,
            "slippageIncluded": False,
            "pnlBasis": "gross before brokerage, taxes, fees and slippage",
            "decisionScoringRecomputed": False,
            "historyLimit": (
                "decision snapshots may span the configured archive, but fill pricing "
                f"is limited to the latest {VELOCITY_RETENTION_MINUTES} minutes of rolling LTP history"
            ),
        }


class LtpHistory:
    """Point-in-time CE/PE LTP lookups for one symbol, sourced from the
    bounded live-velocity parquet. This is intentionally a short rolling
    window, so older decision snapshots remain inspectable but cannot be
    reliably priced by this replay model."""

    def __init__(self, symbol: str, log_path: str = JSON_HISTORY_LOG_PATH):
        self.symbol = symbol
        self._by_key: dict[tuple, pd.DataFrame] = {}
        if not os.path.exists(log_path):
            return
        df = pd.read_parquet(log_path)
        df = df[df["Symbol"] == symbol].copy()
        if df.empty:
            return
        df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])
        for (expiry, strike), g in df.groupby(["Expiry", "StrikePrice"]):
            self._by_key[(str(expiry), int(round(float(strike))))] = (
                g.sort_values("snapshot_time")[["snapshot_time", "CE_LTP", "PE_LTP"]]
                 .reset_index(drop=True)
            )

    def _series(self, expiry: str, strike: int):
        return self._by_key.get((str(expiry), int(strike)))

    def price_at(self, expiry: str, strike: int, instrument_type: str, ts,
                 max_lookahead_seconds: int = MAX_FILL_LOOKAHEAD_SECONDS) -> Optional[tuple]:
        """Nearest tick AT OR AFTER `ts` (entry fill semantics). Returns
        (fill_timestamp, price) or None if no data exists for this
        instrument at/after ts."""
        g = self._series(expiry, strike)
        if g is None:
            return None
        col = "CE_LTP" if instrument_type == "CE" else "PE_LTP"
        after = g[g["snapshot_time"] >= ts]
        if max_lookahead_seconds is not None:
            after = after[
                after["snapshot_time"] <= ts + timedelta(seconds=max_lookahead_seconds)
            ]
        if after.empty:
            return None
        row = after.iloc[0]
        price = row[col]
        if price is None or pd.isna(price) or price <= 0:
            return None
        return row["snapshot_time"], float(price)

    def last_price_before(self, expiry: str, strike: int, instrument_type: str, ts,
                           session_start=None, session_end=None) -> Optional[tuple]:
        """Latest tick STRICTLY BEFORE `ts` (day-boundary square-off
        semantics). If `session_start`/`session_end` are given, only ticks
        within [session_start, session_end) are eligible — prevents a
        logging gap from letting this reach back across a session
        boundary (either direction) and returning a stale price from a
        different trading day. Returns (timestamp, price) or None."""
        g = self._series(expiry, strike)
        if g is None:
            return None
        col = "CE_LTP" if instrument_type == "CE" else "PE_LTP"
        before = g[g["snapshot_time"] < ts]
        if session_start is not None:
            before = before[before["snapshot_time"] >= session_start]
        if session_end is not None:
            before = before[before["snapshot_time"] < session_end]
        if before.empty:
            return None
        row = before.iloc[-1]
        price = row[col]
        if price is None or pd.isna(price) or price <= 0:
            return None
        return row["snapshot_time"], float(price)


async def run_backtest(
    symbol: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    qty_lots: int = 1,
    min_confidence: int = 40,
    cooldown_seconds: int = 300,
    max_trades_per_symbol_per_day: int = 10,
    use_account_guard: bool = False,
    db_path: Optional[str] = None,
    ltp_log_path: Optional[str] = None,
    override_execute_recommended: bool = False,
) -> BacktestResult:
    """Core async entry point. See run_backtest_sync() for a plain
    synchronous call site (e.g. a notebook or a CLI script).
    db_path/ltp_log_path override the production snapshot/LTP stores —
    mainly for tests; production callers should leave these as None."""
    from backtest import snapshot_logger
    snapshots = load_decision_snapshots(symbol, start, end,
                                         db_path=db_path or snapshot_logger.DB_PATH)
    result = BacktestResult(
        symbol=symbol,
        snapshot_count=len(snapshots),
        data_start=str(snapshots[0]["snapshot_time"]) if snapshots else None,
        data_end=str(snapshots[-1]["snapshot_time"]) if snapshots else None,
        requested_start=start,
        requested_end=end,
    )
    if not snapshots:
        return result
    if override_execute_recommended:
        for row in snapshots:
            decision = json.loads(row["decision_json"])
            decision["executeRecommended"] = decision.get("confidence", 0) >= min_confidence
            row["decision_json"] = json.dumps(decision)

    ltp = LtpHistory(symbol, log_path=ltp_log_path or JSON_HISTORY_LOG_PATH)

    guard = _NoOpGuard()
    guard_tmpdir = None
    if use_account_guard:
        from risk.account_guard import LiveAccountRiskGuard
        guard_tmpdir = tempfile.mkdtemp(prefix="backtest_guard_")
        guard = LiveAccountRiskGuard(
            kill_switch_path=os.path.join(guard_tmpdir, "KILL_SWITCH"),
            db_path=os.path.join(guard_tmpdir, "guard.db"),
        )

    open_position: Optional[SimTrade] = None
    running_daily_pnl = 0.0
    current_day: Optional[str] = None

    async def _submit(sym, instrument_type, expiry, strike, side, lots):
        """Stands in for server/app.py's _submit_auto_order /
        _handle_place_order — fills using this tick's LTP instead of
        hitting a broker. Raises when no price data exists, so
        AutoExecutor.maybe_execute()'s existing failure path (it already
        catches and logs submit_order_fn exceptions) is exercised
        identically to a real broker rejection."""
        nonlocal open_position
        # Fill timestamp comes from _submit.current_ts, set by the loop
        # below right before each maybe_execute() call (same pattern as
        # _clock["ts"] above) — this function's own signature has to
        # match submit_order_fn's fixed shape, so it can't take ts as a
        # parameter directly.
        found = ltp.price_at(expiry, strike, instrument_type, _submit.current_ts)
        if found is None:
            result.unpriced_signals += 1
            raise RuntimeError(f"no LTP data for {sym} {instrument_type} {strike} at/after {_submit.current_ts}")
        fill_time, fill_price = found
        open_position = SimTrade(
            symbol=sym, expiry=expiry, instrument_type=instrument_type, side=side,
            strike=strike, qty_lots=lots,
            lot_size=int(_submit.current_lot_size or 1),
            entry_time=str(fill_time), entry_price=fill_price,
            confidence=_submit.current_confidence,
            bias_strength=_submit.current_bias_strength,
        )
        result.trades.append(open_position)

    _submit.current_ts = None
    _submit.current_lot_size = None
    _submit.current_confidence = None
    _submit.current_bias_strength = None

    # AutoExecutor's cooldown/daily-cap gates read _now_fn()/_today_fn()
    # (see decision/auto_executor.py) — bound to the CURRENT loop tick's
    # simulated timestamp via this mutable holder, same closure pattern
    # as _submit.current_ts above, so those gates are evaluated against
    # real historical time gaps rather than the replay's own near-instant
    # wall-clock runtime.
    _clock = {"ts": None}
    executor = AutoExecutor(
        guard=guard,
        submit_order_fn=_submit,
        enabled=True,  # backtest always evaluates as if the master switch were on
        min_confidence=min_confidence,
        cooldown_seconds=cooldown_seconds,
        max_trades_per_symbol_per_day=max_trades_per_symbol_per_day,
        qty_lots=qty_lots,
        # Historical snapshots are intentionally old in wall-clock terms;
        # replay already binds all other gates to the simulated tick clock.
        enforce_freshness=False,
        now_fn=lambda: _clock["ts"].timestamp(),
        today_fn=lambda: _clock["ts"].strftime("%Y-%m-%d"),
    )

    def _settle(position: SimTrade, ts, reason: str):
        nonlocal running_daily_pnl, open_position
        if reason == "day_boundary_square_off":
            # `ts` here is the NEW day's first observed tick — the
            # position must be settled at the LAST price seen BEFORE
            # that boundary, never at/after it (no overnight holds in
            # this v1 model — see module docstring). price_at(ts) would
            # incorrectly match the new day's own opening price if one
            # happens to exist for this strike, so it's deliberately
            # skipped here rather than tried first.
            # Bound the search to the entry day's actual TRADING SESSION
            # (09:15–15:30), not the full calendar day: without this, an
            # after-hours artifact in the log (server left running past
            # close, dev/test tick, etc.) can be picked up as if it were
            # the real close price — see replay.py module history for the
            # 23:10 PM tick that motivated this.
            entry_day = pd.Timestamp(position.entry_time).normalize()
            session_start = entry_day + MARKET_OPEN_OFFSET
            session_end = entry_day + MARKET_CLOSE_OFFSET
            priced = ltp.last_price_before(position.expiry, position.strike, position.instrument_type, ts,
                                            session_start=session_start, session_end=session_end)
        else:
            priced = ltp.price_at(position.expiry, position.strike, position.instrument_type, ts)
            if priced is None:
                priced = ltp.last_price_before(position.expiry, position.strike, position.instrument_type, ts)
        if priced is None:
            # Genuinely no price data anywhere near this exit — leave the
            # trade open/unpriced rather than guessing (see module docstring).
            return
        exit_time, exit_price = priced
        position.close(str(exit_time), exit_price, reason)
        running_daily_pnl += position.pnl
        guard.update_pnl(running_daily_pnl)
        open_position = None

    for row in snapshots:
        ts = pd.Timestamp(row["snapshot_time"])
        _clock["ts"] = ts
        day = ts.strftime("%Y-%m-%d")
        if current_day is not None and day != current_day and open_position is not None:
            _settle(open_position, ts, reason="day_boundary_square_off")
        if current_day != day:
            current_day = day
            running_daily_pnl = 0.0

        decision = json.loads(row["decision_json"])
        expiry = row["expiry"]

        exited_this_tick = False
        if open_position is not None:
            action = decision.get("actionType")
            mapping = _ACTION_INSTRUMENT_SIDE.get(action)
            is_opposite = (
                mapping is not None
                and mapping[0] == open_position.instrument_type
                and mapping[1] == _OPPOSITE_SIDE[open_position.side]
                and decision.get("executeRecommended")
                and not decision.get("conflictFlag")
            )
            if is_opposite:
                _settle(open_position, ts, reason="opposite_signal")
                exited_this_tick = True

        # A tick that just closed a position via an opposite signal is
        # NOT also treated as a fresh entry on the flipped side — that
        # would be an unrealistic same-tick round-trip rather than "the
        # algo changed its mind and got back in on the next read." The
        # next tick is free to re-enter, subject to AutoExecutor's own
        # cooldown, exactly as it would live.
        # Entries are also gated to the real trading session: live, the
        # broker WS simply has no ticks outside 09:15-15:30, so no order
        # could ever fire then. The replay must not enter trades on
        # after-hours artifacts in the decision-snapshot log (server left
        # running past close, dev/test ticks, etc.) — see replay.py
        # module history for the concrete bogus after-hours entry
        # (16:37 IST) this guard was added to stop.
        if open_position is None and not exited_this_tick and _in_market_hours(ts):
            _submit.current_ts = ts
            _submit.current_lot_size = row.get("lot_size")
            # Captured here (the tick that decided to enter), not re-derived
            # at settle time, so a bucket reflects what the engine believed
            # AT ENTRY even if later ticks' confidence drifted.
            _submit.current_confidence = decision.get("confidence")
            _submit.current_bias_strength = decision.get("biasStrength")
            await executor.maybe_execute(decision, symbol, expiry)

    if open_position is not None:
        last_ts = pd.Timestamp(snapshots[-1]["snapshot_time"])
        _settle(open_position, last_ts + timedelta(seconds=1), reason="data_exhausted")

    return result


def run_backtest_sync(*args, **kwargs) -> BacktestResult:
    return asyncio.run(run_backtest(*args, **kwargs))


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    res = run_backtest_sync(sym, use_account_guard=True)
    print(f"[backtest] {sym}: {res.summary()}")
    print(f"[backtest] {sym} by confidence bucket:")
    for b in res.summary_by_confidence_bucket():
        print(f"  {b['range']:>22}: n={b['num_trades']:<4} "
              f"win_rate={b['win_rate']} avg_pnl={b['avg_pnl']} total_pnl={b['total_pnl']}")
    for t in res.trades:
        print(f"  {t.entry_time} {t.side} {t.instrument_type} {t.strike} @ {t.entry_price} "
              f"-> {t.exit_time} @ {t.exit_price} pnl={t.pnl} ({t.exit_reason}) "
              f"conf={t.confidence} strength={t.bias_strength}")
