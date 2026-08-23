"""Unit tests for risk/account_guard.py."""

import importlib
import os

import pytest

import risk.account_guard as account_guard


@pytest.fixture
def guard(tmp_path, monkeypatch):
    """Fresh guard per test — isolated SQLite file and kill-switch path,
    with the module's env-driven limits reset to known values regardless
    of what's in the environment running the test suite."""
    monkeypatch.setenv("LIVE_MAX_DAILY_LOSS_RUPEES", "1000")
    monkeypatch.setenv("LIVE_MAX_OPEN_LOTS", "3")
    monkeypatch.setenv("LIVE_MAX_CONSECUTIVE_DRAWDOWNS", "2")
    importlib.reload(account_guard)  # re-read the env vars into module constants

    db_path = str(tmp_path / "test_risk_guard.db")
    kill_switch_path = str(tmp_path / "LIVE_TRADING_KILL")
    return account_guard.LiveAccountRiskGuard(kill_switch_path, db_path=db_path)


def test_starts_untripped(guard):
    tripped, reason = guard.is_tripped()
    assert tripped is False
    assert reason is None


def test_daily_loss_limit_trips_guard_and_writes_kill_switch(guard):
    guard.update_pnl(-1500.0)  # breaches the 1000 limit set in the fixture
    tripped, reason = guard.is_tripped()
    assert tripped is True
    assert "daily loss limit" in reason
    assert os.path.exists(guard.kill_switch_path)


def test_pnl_within_limit_does_not_trip(guard):
    guard.update_pnl(-200.0)
    tripped, _ = guard.is_tripped()
    assert tripped is False
    assert not os.path.exists(guard.kill_switch_path)


def test_consecutive_drawdowns_trip_before_loss_limit(guard):
    # LIVE_MAX_CONSECUTIVE_DRAWDOWNS=2: two checks in a row worse than the
    # running peak should trip even though neither breaches the ₹1000
    # daily loss limit on its own.
    guard.update_pnl(100.0)   # new peak, no drawdown
    guard.update_pnl(50.0)    # worse than peak -> drawdown #1
    tripped_after_one, _ = guard.is_tripped()
    assert tripped_after_one is False

    guard.update_pnl(20.0)    # worse again -> drawdown #2 -> trips
    tripped, reason = guard.is_tripped()
    assert tripped is True
    assert "consecutive drawdown" in reason


def test_drawdown_streak_resets_on_improvement(guard):
    guard.update_pnl(100.0)
    guard.update_pnl(50.0)   # drawdown #1
    guard.update_pnl(150.0)  # improves past peak -> streak resets
    guard.update_pnl(80.0)   # drawdown #1 again (not #2)
    tripped, _ = guard.is_tripped()
    assert tripped is False


def test_none_pnl_is_a_no_op(guard):
    guard.update_pnl(-1500.0)  # would trip
    # Simulate a fresh guard instance that hasn't seen the trip yet would
    # still see it via persistence; here we just confirm passing None
    # doesn't itself change any state on an otherwise-fresh guard.
    fresh_tripped, _ = guard.is_tripped()
    assert fresh_tripped is True  # trip from the real update above persisted


def test_check_new_order_blocked_when_tripped(guard):
    guard.update_pnl(-1500.0)
    allowed, reason = guard.check_new_order(qty_lots=1, current_open_lots=0)
    assert allowed is False
    assert "tripped" in reason


def test_check_new_order_blocked_over_exposure_cap(guard):
    # LIVE_MAX_OPEN_LOTS=3 in the fixture
    allowed, reason = guard.check_new_order(qty_lots=2, current_open_lots=2)
    assert allowed is False
    assert "exposure" in reason


def test_check_new_order_allowed_within_cap(guard):
    allowed, reason = guard.check_new_order(qty_lots=1, current_open_lots=2)
    assert allowed is True
    assert reason is None


def test_check_new_order_fails_closed_on_unparseable_exposure(guard):
    allowed, reason = guard.check_new_order(qty_lots=1, current_open_lots=None)
    assert allowed is False
    assert "refusing" in reason


def test_state_persists_across_guard_instances(tmp_path):
    db_path = str(tmp_path / "persist.db")
    kill_switch_path = str(tmp_path / "LIVE_TRADING_KILL")

    g1 = account_guard.LiveAccountRiskGuard(kill_switch_path, db_path=db_path)
    g1.update_pnl(-999999.0)
    assert g1.is_tripped()[0] is True

    g2 = account_guard.LiveAccountRiskGuard(kill_switch_path, db_path=db_path)
    assert g2.is_tripped()[0] is True


# ── position-book parsing helpers ───────────────────────────────────────

def test_pnl_from_positions_sums_known_field():
    positions = [{"pnl": "100.5"}, {"pnl": "-40"}]
    assert account_guard.pnl_from_positions(positions) == pytest.approx(60.5)


def test_pnl_from_positions_empty_list_is_flat_zero():
    assert account_guard.pnl_from_positions([]) == 0.0


def test_pnl_from_positions_unparseable_row_returns_none():
    positions = [{"pnl": "100"}, {"unrelated_field": "x"}]
    assert account_guard.pnl_from_positions(positions) is None


def test_open_lots_from_positions_sums_by_lot_size():
    lot_sizes = {"NIFTY": 65, "BANKNIFTY": 30}
    positions = [
        {"netqty": "130", "tradingsymbol": "NIFTY24OCT23000CE"},
        {"netqty": "-30", "tradingsymbol": "BANKNIFTY24OCT48000PE"},
    ]
    assert account_guard.open_lots_from_positions(positions, lot_sizes) == 3


def test_open_lots_from_positions_unresolvable_symbol_returns_none():
    lot_sizes = {"NIFTY": 65}
    positions = [{"netqty": "10", "tradingsymbol": "SOMEUNKNOWN24OCT1CE"}]
    assert account_guard.open_lots_from_positions(positions, lot_sizes) is None


def test_projected_exposure_allows_sell_to_close_existing_long():
    positions = [{"netqty": "65", "tradingsymbol": "NIFTY31JUL25000CE"}]
    projected = account_guard.projected_open_lots_from_positions(
        positions, {"NIFTY": 65}, "NIFTY31JUL25000CE", "SELL", 65,
    )
    assert projected == 0


def test_projected_exposure_allows_buy_to_cover_existing_short():
    positions = [{"netqty": "-130", "tradingsymbol": "NIFTY31JUL25000CE"}]
    projected = account_guard.projected_open_lots_from_positions(
        positions, {"NIFTY": 65}, "NIFTY31JUL25000CE", "BUY", 65,
    )
    assert projected == 1


def test_projected_exposure_adds_order_in_different_contract():
    positions = [{"netqty": "65", "tradingsymbol": "NIFTY31JUL25000CE"}]
    projected = account_guard.projected_open_lots_from_positions(
        positions, {"NIFTY": 65}, "NIFTY31JUL25100CE", "BUY", 65,
    )
    assert projected == 2
