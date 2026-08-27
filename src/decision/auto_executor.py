"""
decision/auto_executor.py
--------------------------
Strategy -> execution bridge. This is the piece that turns the dashboard
from a decision-support terminal into an actual algo: everything up to
this module (decision_engine.py's DecisionResult) already computes a
bias/confidence/action every tick, but nothing acted on it — every live
order still required a human to click a confirm button in the UI
(_handle_place_order's `confirmed=true` requirement in server/app.py).

This module does NOT touch that manual path at all. It adds a second,
parallel path: AutoExecutor.evaluate() looks at each tick's DecisionResult
and decides whether it clears a strict bar to auto-submit an order. When
it does, it calls the SAME `submit_order_fn` callback server/app.py
wires up — which is just `_handle_place_order` with `live=True,
confirmed=True` filled in on the algo's behalf — so an auto-executed order
goes through EXACTLY the same checks a manual one does: lot-size
verification, per-order rate limit, and every risk/account_guard.py check
(daily loss limit, exposure cap, drawdown streak). This module adds
GATES on top of that, it does not replace any of it.

Master switch: AUTO_STRATEGY_EXECUTION_ENABLED (env, default false). This
is INDEPENDENT of server/app.py's LIVE_TRADING_ENABLED — both must be
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

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from decision.types import T

logger = logging.getLogger(__name__)

AUTO_STRATEGY_EXECUTION_ENABLED = (
    os.environ.get("AUTO_STRATEGY_EXECUTION_ENABLED", "").strip().lower() == "true"
)
AUTO_TRADE_COOLDOWN_SECONDS = int(os.environ.get("AUTO_TRADE_COOLDOWN_SECONDS", "300"))
AUTO_TRADE_MAX_PER_SYMBOL_PER_DAY = int(os.environ.get("AUTO_TRADE_MAX_PER_SYMBOL_PER_DAY", "10"))
AUTO_TRADE_QTY_LOTS = int(os.environ.get("AUTO_TRADE_QTY_LOTS", "1"))
# Cap on the in-memory auto-trade history feed (see AutoExecutor._history
# below) — this is a "what did the algo attempt and why" display list for
# the dashboard, not an audit trail of record (the broker's own order book
# and paper_trading.py's SQLite log are that), so an unbounded list isn't
# needed and would just grow forever across a long-running process.
AUTO_TRADE_HISTORY_MAX = int(os.environ.get("AUTO_TRADE_HISTORY_MAX", "200"))
AUTO_DECISION_MAX_AGE_SECONDS = int(os.environ.get("AUTO_DECISION_MAX_AGE_SECONDS", "30"))

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
    """One instance, created once at server/app.py startup alongside
    _ACCOUNT_GUARD, and called once per tick with that tick's decision
    block (already computed by DecisionEngine inside mTerminals_json.py —
    this module never calls DecisionEngine itself, it only consumes its
    output)."""

    def __init__(
        self,
        guard,  # risk.account_guard.LiveAccountRiskGuard — see module docstring
        submit_order_fn: Callable[[str, str, str, int, str, int], Awaitable[dict]],
        enabled: bool = AUTO_STRATEGY_EXECUTION_ENABLED,
        min_confidence: int = T.CONFIDENCE_EXECUTE_MIN,
        cooldown_seconds: int = AUTO_TRADE_COOLDOWN_SECONDS,
        max_trades_per_symbol_per_day: int = AUTO_TRADE_MAX_PER_SYMBOL_PER_DAY,
        qty_lots: int = AUTO_TRADE_QTY_LOTS,
        max_decision_age_seconds: int = AUTO_DECISION_MAX_AGE_SECONDS,
        enforce_freshness: bool = True,
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
        self.max_decision_age_seconds = max_decision_age_seconds
        self.enforce_freshness = enforce_freshness
        # Pluggable clock, defaulting to the real wall clock — every live
        # call site (server/app.py) never passes these, so production
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

        # Last evaluate() outcome per symbol, purely for status reporting
        # (e.g. the frontend's algo status panel) — evaluate()/maybe_execute()
        # already compute a human-readable ExecutionDecision.reason on every
        # tick, this just retains the most recent one instead of letting it
        # only ever reach a log line. Not consulted by any gating logic
        # itself, so it's safe to read from another coroutine/thread without
        # locking (worst case a status read sees the previous tick's value).
        self._last_decision: dict[str, ExecutionDecision] = {}

        # Rolling history of ACTUAL EXECUTION ATTEMPTS only (evaluate()
        # returned should_execute=True) — not every tick's evaluate() miss.
        # A tick that WAITs, fails confidence, or is in cooldown happens
        # continuously and is already summarized by _last_decision above;
        # recording every one of those here would make this list nothing
        # but noise. This is specifically "what did the algo actually try
        # to do, and did it go through" — the input to trusting/distrusting
        # auto-execution, not a tick-by-tick decision trace. Newest entry
        # first (list.insert(0, ...)), capped at AUTO_TRADE_HISTORY_MAX.
        self._history: list[dict] = []
        # Gate evaluation and broker submission as one critical section.
        # Otherwise a second tick can clear cooldown/daily-cap checks while
        # the first tick is still awaiting its broker response.
        self._execution_lock = asyncio.Lock()

    def _record_history(self, symbol: str, outcome: "ExecutionDecision", status: str, detail: str):
        """status is 'executed' (submit_order_fn succeeded — the order
        actually reached the broker) or 'rejected' (submit_order_fn raised,
        e.g. a downstream account_guard/kill-switch/resolve failure that
        happened AFTER evaluate() cleared — see _submit_auto_order's own
        comment on why that distinction now exists). detail is the
        rejection exception's message for 'rejected', or the same
        cleared-reason evaluate() already produced for 'executed'."""
        self._history.insert(0, {
            "ts": self._now_fn(),
            "symbol": symbol,
            "side": outcome.side,
            "instrument_type": outcome.instrument_type,
            "strike": outcome.strike,
            "qty_lots": self.qty_lots,
            "status": status,
            "reason": detail,
        })
        del self._history[AUTO_TRADE_HISTORY_MAX:]

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
            outcome = ExecutionDecision(False, "auto-execution disabled (AUTO_STRATEGY_EXECUTION_ENABLED=false)")
            self._last_decision[symbol] = outcome
            return outcome

        outcome = self._evaluate_inner(decision, symbol)
        self._last_decision[symbol] = outcome
        return outcome

    def _evaluate_inner(self, decision: dict, symbol: str) -> ExecutionDecision:
        """The actual gating chain, split out of evaluate() so every exit
        path (including the master-switch short-circuit above) funnels
        through one place that records _last_decision — see evaluate()."""
        if decision.get("degraded"):
            missing = ", ".join(decision.get("missingInputs") or []) or "unspecified inputs"
            return ExecutionDecision(False, f"degraded decision — missing: {missing}")

        if decision.get("stale"):
            return ExecutionDecision(False, "stale decision state")

        if self.enforce_freshness:
            timestamp = decision.get("decisionTimestamp")
            if not timestamp:
                return ExecutionDecision(False, "decision timestamp missing — freshness cannot be verified")
            try:
                decision_ts = datetime.fromisoformat(str(timestamp)).timestamp()
            except (TypeError, ValueError):
                return ExecutionDecision(False, "decision timestamp invalid — freshness cannot be verified")
            age = self._now_fn() - decision_ts
            if age < -5:
                return ExecutionDecision(False, "decision timestamp is in the future")
            if age > self.max_decision_age_seconds:
                return ExecutionDecision(False, f"stale decision — age {age:.0f}s exceeds {self.max_decision_age_seconds}s")

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

    def get_status(self, symbol: str) -> dict:
        """Read-only snapshot for status reporting (e.g. server/app.py's
        algoStatus broadcast) — does not affect gating, safe to call from
        any coroutine. `last_decision_reason` mirrors the most recent
        evaluate() outcome, which is already the same human-readable
        string logged on every tick, just retained instead of only ever
        reaching a log line."""
        self._roll_day_if_needed()
        last = self._last_decision.get(symbol)
        return {
            "enabled": self.enabled,
            "min_confidence": self.min_confidence,
            "cooldown_seconds": self.cooldown_seconds,
            "max_trades_per_symbol_per_day": self.max_trades_per_symbol_per_day,
            "qty_lots": self.qty_lots,
            "max_decision_age_seconds": self.max_decision_age_seconds,
            "enforce_freshness": self.enforce_freshness,
            "trades_today": self._trade_count_today.get(symbol, 0),
            "last_execution_ts": self._last_execution_ts.get(symbol),
            "last_decision_should_execute": last.should_execute if last else None,
            "last_decision_reason": last.reason if last else None,
        }

    def get_history(self) -> list[dict]:
        """Read-only snapshot of the auto-trade attempt feed (see
        _record_history's docstring for what qualifies) — newest first.
        Returns shallow copies so a caller mutating the returned list/dicts
        can't corrupt this instance's own history."""
        return [dict(entry) for entry in self._history]

    async def maybe_execute(self, decision: dict, symbol: str, expiry: str) -> ExecutionDecision:
        """Call once per tick with that tick's decision block. Evaluates,
        and if cleared, submits via submit_order_fn — the account_guard's
        own checks (exposure, trip state) still run again downstream
        inside that callback since it's the same path a manual order
        takes; this method's guard check above is a fast pre-filter, not
        a replacement for that."""
        async with self._execution_lock:
            return await self._maybe_execute_locked(decision, symbol, expiry)

    async def _maybe_execute_locked(self, decision: dict, symbol: str, expiry: str) -> ExecutionDecision:
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
            self._record_history(symbol, outcome, "executed", outcome.reason)
            logger.info(f"[auto_executor] EXECUTED {symbol} {outcome.side} {outcome.instrument_type} "
                        f"{outcome.strike} — {outcome.reason}")
            print(f"[auto_executor] EXECUTED {symbol} {outcome.side} {outcome.instrument_type} "
                  f"{outcome.strike} — {outcome.reason}", flush=True)
        except Exception as e:
            self._record_history(symbol, outcome, "rejected", str(e))
            logger.error(f"[auto_executor] submit_order_fn raised for {symbol}: {e}")
            print(f"[auto_executor] FAILED to submit {symbol} {outcome.side} {outcome.instrument_type} "
                  f"{outcome.strike}: {e}", flush=True)
            return ExecutionDecision(False, f"submission failed: {e}")

        return outcome
