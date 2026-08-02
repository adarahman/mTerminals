"""Unit tests for decision/auto_executor.py's AutoExecutor.evaluate()."""

import asyncio

import pytest

from decision.auto_executor import AutoExecutor, ExecutionDecision


class _FakeGuard:
    def __init__(self, tripped=False, reason=None):
        self._tripped = tripped
        self._reason = reason

    def is_tripped(self):
        return self._tripped, self._reason


def _good_decision(**overrides):
    d = {
        "actionType": "SELL_CE",
        "conflictFlag": False,
        "executeRecommended": True,
        "confidence": 70,
        "suggestedStrike": 24000,
        "strategyCaution": "",
    }
    d.update(overrides)
    return d


def _executor(**kwargs):
    kwargs.setdefault("guard", _FakeGuard())
    kwargs.setdefault("submit_order_fn", lambda *a, **k: None)
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("min_confidence", 40)
    kwargs.setdefault("cooldown_seconds", 300)
    kwargs.setdefault("max_trades_per_symbol_per_day", 10)
    return AutoExecutor(**kwargs)


def test_disabled_by_default_blocks_everything():
    ex = _executor(enabled=False)
    outcome = ex.evaluate(_good_decision(), "NIFTY")
    assert outcome.should_execute is False
    assert "disabled" in outcome.reason


def test_clears_on_a_clean_single_leg_decision():
    ex = _executor()
    outcome = ex.evaluate(_good_decision(), "NIFTY")
    assert outcome.should_execute is True
    assert outcome.instrument_type == "CE"
    assert outcome.side == "SELL"
    assert outcome.strike == 24000


def test_wait_action_never_executes():
    ex = _executor()
    outcome = ex.evaluate(_good_decision(actionType="WAIT"), "NIFTY")
    assert outcome.should_execute is False
    assert "WAIT" in outcome.reason or "not auto-executable" in outcome.reason


@pytest.mark.parametrize("multi_leg", ["SPREAD_BEAR", "SPREAD_BULL", "STRADDLE", "STRANGLE", "CONDOR"])
def test_multi_leg_actions_never_execute_v1(multi_leg):
    ex = _executor()
    outcome = ex.evaluate(_good_decision(actionType=multi_leg), "NIFTY")
    assert outcome.should_execute is False
    assert "not auto-executable" in outcome.reason


def test_conflict_flag_blocks():
    ex = _executor()
    outcome = ex.evaluate(_good_decision(conflictFlag=True), "NIFTY")
    assert outcome.should_execute is False
    assert "conflict" in outcome.reason


def test_execute_not_recommended_blocks():
    ex = _executor()
    outcome = ex.evaluate(_good_decision(executeRecommended=False, strategyCaution="low DTE"), "NIFTY")
    assert outcome.should_execute is False
    assert "low DTE" in outcome.reason


def test_confidence_below_threshold_blocks():
    ex = _executor(min_confidence=60)
    outcome = ex.evaluate(_good_decision(confidence=50), "NIFTY")
    assert outcome.should_execute is False
    assert "confidence" in outcome.reason


def test_missing_strike_blocks():
    ex = _executor()
    outcome = ex.evaluate(_good_decision(suggestedStrike=None), "NIFTY")
    assert outcome.should_execute is False
    assert "strike" in outcome.reason


def test_tripped_guard_blocks():
    ex = _executor(guard=_FakeGuard(tripped=True, reason="daily loss limit breached"))
    outcome = ex.evaluate(_good_decision(), "NIFTY")
    assert outcome.should_execute is False
    assert "tripped" in outcome.reason


def test_cooldown_blocks_repeat_execution():
    ex = _executor(cooldown_seconds=9999)
    ex._last_execution_ts["NIFTY"] = __import__("time").time()
    outcome = ex.evaluate(_good_decision(), "NIFTY")
    assert outcome.should_execute is False
    assert "cooldown" in outcome.reason


def test_cooldown_is_per_symbol():
    ex = _executor(cooldown_seconds=9999)
    ex._last_execution_ts["BANKNIFTY"] = __import__("time").time()
    outcome = ex.evaluate(_good_decision(), "NIFTY")
    assert outcome.should_execute is True


def test_daily_trade_cap_blocks():
    ex = _executor(max_trades_per_symbol_per_day=2)
    ex._roll_day_if_needed()
    ex._trade_count_today["NIFTY"] = 2
    outcome = ex.evaluate(_good_decision(), "NIFTY")
    assert outcome.should_execute is False
    assert "cap" in outcome.reason


def test_action_type_maps_to_correct_side_and_instrument():
    ex = _executor()
    cases = {
        "BUY_CE":  ("CE", "BUY"),
        "BUY_PE":  ("PE", "BUY"),
        "SELL_CE": ("CE", "SELL"),
        "SELL_PE": ("PE", "SELL"),
    }
    for action_type, (instrument, side) in cases.items():
        outcome = ex.evaluate(_good_decision(actionType=action_type), "NIFTY")
        assert outcome.should_execute is True
        assert outcome.instrument_type == instrument
        assert outcome.side == side


# ── maybe_execute() — the async submit path ─────────────────────────────

def test_maybe_execute_calls_submit_fn_and_updates_state():
    calls = []

    async def fake_submit(symbol, instrument_type, expiry, strike, side, qty_lots):
        calls.append((symbol, instrument_type, expiry, strike, side, qty_lots))

    ex = _executor(submit_order_fn=fake_submit, qty_lots=2)
    outcome = asyncio.run(ex.maybe_execute(_good_decision(), "NIFTY", "28AUG2026"))

    assert outcome.should_execute is True
    assert calls == [("NIFTY", "CE", "28AUG2026", 24000, "SELL", 2)]
    assert "NIFTY" in ex._last_execution_ts
    assert ex._trade_count_today["NIFTY"] == 1


def test_maybe_execute_does_not_update_state_when_gated():
    async def fake_submit(*a, **k):
        raise AssertionError("should not be called when gated")

    ex = _executor(submit_order_fn=fake_submit, enabled=False)
    outcome = asyncio.run(ex.maybe_execute(_good_decision(), "NIFTY", "28AUG2026"))
    assert outcome.should_execute is False
    assert "NIFTY" not in ex._last_execution_ts


def test_maybe_execute_handles_submit_failure_gracefully():
    async def failing_submit(*a, **k):
        raise RuntimeError("broker unreachable")

    ex = _executor(submit_order_fn=failing_submit)
    outcome = asyncio.run(ex.maybe_execute(_good_decision(), "NIFTY", "28AUG2026"))
    assert outcome.should_execute is False
    assert "submission failed" in outcome.reason
    # state should not advance on a failed submission
    assert "NIFTY" not in ex._last_execution_ts
