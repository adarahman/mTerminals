"""
decision/auto_executor.py
--------------------------
Strategy -> execution bridge. This is the piece that turns the dashboard
from a decision-support terminal into an actual algo: everything up to
this module (decision_engine.py's DecisionResult) already computes a
bias/confidence/action every tick, but nothing acted on it — every live
order still required a human to click a confirm button in the UI
(_handle_place_order's `confirmed=true` requirement in ws_server_live.py).

This module does NOT touch that manual path at all. It adds a second,
parallel path: AutoExecutor.evaluate() looks at each tick's DecisionResult
and decides whether it clears a strict bar to auto-submit an order. When
it does, it calls the SAME `submit_order_fn` callback ws_server_live.py
wires up — which is just `_handle_place_order` with `live=True,
confirmed=True` filled in on the algo's behalf — so an auto-executed order
goes through EXACTLY the same checks a manual one does: lot-size
verification, per-order rate limit, and every risk/account_guard.py check
(daily loss limit, exposure cap, drawdown streak). This module adds
GATES on top of that, it does not replace any of it.

Master switch: AUTO_STRATEGY_EXECUTION_ENABLED (env, default false). This
is INDEPENDENT of ws_server_live.py's LIVE_TRADING_ENABLED — both must be
true for an auto-executed order to reach the real broker. Same
double-opt-in pattern as the rest of the live-trading config.

Scope of v1 — deliberately narrow:
  - Only single-leg action_types are auto-executed: BUY_CE, BUY_PE,
    SELL_CE, SELL_PE. Multi-leg suggestions (SPREAD_BEAR, SPREAD_BULL,
    STRADDLE, STRANGLE, CONDOR) are surfaced in `auto_strategy` for the
    dashboard to show, but this module does not attempt to auto-execute
    them — a multi-leg order needs atomic multi-order submission with
    its own partial-fill handling, which doesn't exist yet. Same
    refuse-rather-than-guess posture as lot_sizes.py.
  - Cooldown and per-day trade-count state are IN-MEMORY, not persisted
    (unlike risk/account_guard.py's SQLite state). A server restart
    resets them. The persisted account-level guard (daily loss, exposure,
    drawdown streak) is the actual backstop that survives a restart;
    cooldown/trade-count here are a secondary throttle on top of it, not
    the primary safety mechanism.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from decision.types import T

logger = logging.getLogger(__name__)

AUTO_STRATEGY_EXECUTION_ENABLED = (
    os.environ.get("AUTO_STRATEGY_EXECUTION_ENABLED", "").strip().lower() == "true"
)
AUTO_TRADE_COOLDOWN_SECONDS = int(os.environ.get("AUTO_TRADE_COOLDOWN_SECONDS", "300"))
AUTO_TRADE_MAX_PER_SYMBOL_PER_DAY = int(os.environ.get("AUTO_TRADE_MAX_PER_SYMBOL_PER_DAY", "10"))
AUTO_TRADE_QTY_LOTS = int(os.environ.get("AUTO_TRADE_QTY_LOTS", "1"))

# action_type -> (instrument_type, side). WAIT and every multi-leg
# action_type are intentionally absent — see module docstring.
_SINGLE_LEG_ACTIONS = {
    "BUY_CE":  ("CE", "BUY"),
    "BUY_PE":  ("PE", "BUY"),
    "SELL_CE": ("CE", "SELL"),
    "SELL_PE": ("PE", "SELL"),
}


@dataclass
class ExecutionDecision:
    should_execute: bool
    reason: str
    instrument_type: Optional[str] = None
    side: Optional[str] = None
    strike: Optional[int] = None


class AutoExecutor:
    """One instance, created once at ws_server_live.py startup alongside
    _ACCOUNT_GUARD, and called once per tick with that tick's decision
    block (already computed by DecisionEngine inside mTerminals_json.py —
    this module never calls DecisionEngine itself, it only consumes its
    output)."""

    def __init__(
        self,
        guard,  # risk.account_guard.LiveAccountRiskGuard — see module docstring
        submit_order_fn: Callable[[str, str, str, int, str, int], Awaitable[None]],
        enabled: bool = AUTO_STRATEGY_EXECUTION_ENABLED,
        min_confidence: int = T.CONFIDENCE_EXECUTE_MIN,
        cooldown_seconds: int = AUTO_TRADE_COOLDOWN_SECONDS,
        max_trades_per_symbol_per_day: int = AUTO_TRADE_MAX_PER_SYMBOL_PER_DAY,
        qty_lots: int = AUTO_TRADE_QTY_LOTS,
        now_fn: Callable[[], float] = time.time,
        today_fn: Callable[[], str] = lambda: time.strftime("%Y-%m-%d"),
    ):
        self.guard = guard
        self.submit_order_fn = submit_order_fn
        self.enabled = enabled
        self.min_confidence = min_confidence
        self.cooldown_seconds = cooldown_seconds
        self.max_trades_per_symbol_per_day = max_trades_per_symbol_per_day
        self.qty_lots = qty_lots
        # Pluggable clock, defaulting to the real wall clock — every live
        # call site (ws_server_live.py) never passes these, so production
        # behavior is unchanged. backtest/replay.py injects functions
        # bound to each tick's SIMULATED timestamp instead, so cooldown
        # and the daily-trade-cap rollover get evaluated against real
        # historical time gaps rather than the backtest process's own
        # (near-instant) wall-clock runtime. Without this, replaying a
        # week of history in under a second would either never clear a
        # cooldown or never roll the trade-count day over, and the
        # backtest would silently validate nothing about either gate.
        self._now_fn = now_fn
        self._today_fn = today_fn

        # In-memory only — see module docstring.
        self._last_execution_ts: dict[str, float] = {}
        self._trade_count_today: dict[str, int] = {}
        self._count_day: Optional[str] = None

    def _roll_day_if_needed(self):
        today = self._today_fn()
        if self._count_day != today:
            self._count_day = today
            self._trade_count_today = {}

    def evaluate(self, decision: dict, symbol: str) -> ExecutionDecision:
        """Pure decision — does NOT submit anything. Call
        maybe_execute() to actually act on the result. Split out so the
        gating logic is unit-testable without an event loop or a fake
        broker."""
        if not self.enabled:
            return ExecutionDecision(False, "auto-execution disabled (AUTO_STRATEGY_EXECUTION_ENABLED=false)")

        action_type = decision.get("actionType", "WAIT")
        if action_type not in _SINGLE_LEG_ACTIONS:
            return ExecutionDecision(False, f"action_type '{action_type}' not auto-executable (WAIT or multi-leg — v1 scope)")

        if decision.get("conflictFlag"):
            return ExecutionDecision(False, "conflict_flag set — sub-signals disagree")

        if not decision.get("executeRecommended", False):
            return ExecutionDecision(False, f"execute not recommended: {decision.get('strategyCaution', '(no reason given)')}")

        confidence = decision.get("confidence", 0)
        if confidence < self.min_confidence:
            return ExecutionDecision(False, f"confidence {confidence} below threshold {self.min_confidence}")

        strike = decision.get("suggestedStrike")
        if strike is None:
            return ExecutionDecision(False, "no suggested strike on this decision")

        tripped, trip_reason = self.guard.is_tripped()
        if tripped:
            return ExecutionDecision(False, f"account risk guard tripped: {trip_reason}")

        self._roll_day_if_needed()
        last_ts = self._last_execution_ts.get(symbol)
        now = self._now_fn()
        if last_ts is not None and (now - last_ts) < self.cooldown_seconds:
            remaining = self.cooldown_seconds - (now - last_ts)
            return ExecutionDecision(False, f"cooldown active — {remaining:.0f}s remaining for {symbol}")

        count_today = self._trade_count_today.get(symbol, 0)
        if count_today >= self.max_trades_per_symbol_per_day:
            return ExecutionDecision(False, f"daily auto-trade cap reached for {symbol} ({count_today}/{self.max_trades_per_symbol_per_day})")

        instrument_type, side = _SINGLE_LEG_ACTIONS[action_type]
        return ExecutionDecision(True, f"cleared: {action_type} confidence={confidence}",
                                  instrument_type=instrument_type, side=side, strike=int(strike))

    async def maybe_execute(self, decision: dict, symbol: str, expiry: str) -> ExecutionDecision:
        """Call once per tick with that tick's decision block. Evaluates,
        and if cleared, submits via submit_order_fn — the account_guard's
        own checks (exposure, trip state) still run again downstream
        inside that callback since it's the same path a manual order
        takes; this method's guard check above is a fast pre-filter, not
        a replacement for that."""
        outcome = self.evaluate(decision, symbol)
        if not outcome.should_execute:
            return outcome

        try:
            await self.submit_order_fn(
                symbol, outcome.instrument_type, expiry, outcome.strike,
                outcome.side, self.qty_lots,
            )
            self._last_execution_ts[symbol] = self._now_fn()
            self._trade_count_today[symbol] = self._trade_count_today.get(symbol, 0) + 1
            logger.info(f"[auto_executor] EXECUTED {symbol} {outcome.side} {outcome.instrument_type} "
                        f"{outcome.strike} — {outcome.reason}")
            print(f"[auto_executor] EXECUTED {symbol} {outcome.side} {outcome.instrument_type} "
                  f"{outcome.strike} — {outcome.reason}", flush=True)
        except Exception as e:
            logger.error(f"[auto_executor] submit_order_fn raised for {symbol}: {e}")
            print(f"[auto_executor] FAILED to submit {symbol} {outcome.side} {outcome.instrument_type} "
                  f"{outcome.strike}: {e}", flush=True)
            return ExecutionDecision(False, f"submission failed: {e}")

        return outcome
