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
    ws_server_live.py's live path does after every real fill.

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
from datetime import datetime
from typing import Optional

import pandas as pd

from decision.auto_executor import AutoExecutor
from backtest.snapshot_logger import load_decision_snapshots
from oi.oi_analysis import JSON_HISTORY_LOG_PATH

_OPPOSITE_SIDE = {"BUY": "SELL", "SELL": "BUY"}
_ACTION_INSTRUMENT_SIDE = {
    "BUY_CE": ("CE", "BUY"), "BUY_PE": ("PE", "BUY"),
    "SELL_CE": ("CE", "SELL"), "SELL_PE": ("PE", "SELL"),
}


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


class LtpHistory:
    """Point-in-time CE/PE LTP lookups for one symbol, sourced from
    oi/oi_analysis.py's oi_history_log.parquet (45-day rolling window —
    this bounds how far back a backtest can meaningfully go until more
    days accumulate)."""

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

    def price_at(self, expiry: str, strike: int, instrument_type: str, ts) -> Optional[tuple]:
        """Nearest tick AT OR AFTER `ts` (entry fill semantics). Returns
        (fill_timestamp, price) or None if no data exists for this
        instrument at/after ts."""
        g = self._series(expiry, strike)
        if g is None:
            return None
        col = "CE_LTP" if instrument_type == "CE" else "PE_LTP"
        after = g[g["snapshot_time"] >= ts]
        if after.empty:
            return None
        row = after.iloc[0]
        price = row[col]
        if price is None or pd.isna(price) or price <= 0:
            return None
        return row["snapshot_time"], float(price)

    def last_price_before(self, expiry: str, strike: int, instrument_type: str, ts) -> Optional[tuple]:
        """Latest tick STRICTLY BEFORE `ts` (day-boundary square-off
        semantics). Returns (timestamp, price) or None."""
        g = self._series(expiry, strike)
        if g is None:
            return None
        col = "CE_LTP" if instrument_type == "CE" else "PE_LTP"
        before = g[g["snapshot_time"] < ts]
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
    result = BacktestResult(symbol=symbol)
    if not snapshots:
        return result
    if override_execute_recommended:
        for row in snapshots:
            decision = json.loads(row["decision_json"])
            decision["executeRecommended"] = decision.get("confidence", 0) >= min_confidence
            row["decision_json"] = json.dumps(decision)

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
        """Stands in for ws_server_live.py's _submit_auto_order /
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
        )
        result.trades.append(open_position)

    _submit.current_ts = None
    _submit.current_lot_size = None

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
            priced = ltp.last_price_before(position.expiry, position.strike, position.instrument_type, ts)
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
        if open_position is None and not exited_this_tick:
            _submit.current_ts = ts
            _submit.current_lot_size = row.get("lot_size")
            await executor.maybe_execute(decision, symbol, expiry)

    if open_position is not None:
        last_ts = pd.Timestamp(snapshots[-1]["snapshot_time"])
        _settle(open_position, last_ts + pd.Timedelta(seconds=1), reason="data_exhausted")

    return result


def run_backtest_sync(*args, **kwargs) -> BacktestResult:
    return asyncio.run(run_backtest(*args, **kwargs))


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    res = run_backtest_sync(sym, use_account_guard=True)
    print(f"[backtest] {sym}: {res.summary()}")
    for t in res.trades:
        print(f"  {t.entry_time} {t.side} {t.instrument_type} {t.strike} @ {t.entry_price} "
              f"-> {t.exit_time} @ {t.exit_price} pnl={t.pnl} ({t.exit_reason})")
