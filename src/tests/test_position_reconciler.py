"""Unit tests for risk/position_reconciler.py."""

import importlib
import os

import risk.position_reconciler as position_reconciler


LOT_SIZES = {"NIFTY": 25, "BANKNIFTY": 15}


def _guard(tmp_path, monkeypatch, trip_lots="2"):
    monkeypatch.setenv("LIVE_RECONCILE_TRIP_LOTS", trip_lots)
    monkeypatch.setenv("LIVE_RECONCILE_MIN_ORDER_AGE_SECONDS", "60")
    importlib.reload(position_reconciler)
    kill_switch_path = str(tmp_path / "LIVE_TRADING_KILL")
    return position_reconciler, kill_switch_path


# ── parsing helpers ──────────────────────────────────────────────────────

def test_net_lots_from_orders_sums_buy_and_sell_signed():
    now = 10_000.0
    orders = [
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "BUY",
         "filledshares": "50", "orderstatus": "complete", "updatetime": now - 200},
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "SELL",
         "filledshares": "25", "orderstatus": "complete", "updatetime": now - 200},
    ]
    net, bad = position_reconciler.net_lots_by_symbol_from_orders(
        orders, LOT_SIZES, now_ts=now
    )
    assert net == {"NIFTY24OCT23000CE": 1}  # (50/25) - (25/25) = 2 - 1
    assert bad == []


def test_net_lots_from_orders_ignores_non_complete_status():
    orders = [
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "BUY",
         "filledshares": "50", "orderstatus": "rejected", "updatetime": 100},
    ]
    net, bad = position_reconciler.net_lots_by_symbol_from_orders(
        orders, LOT_SIZES, now_ts=10_000.0
    )
    assert net == {}
    assert bad == []


def test_net_lots_from_orders_excludes_orders_younger_than_min_age():
    now = 10_000.0
    orders = [
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "BUY",
         "filledshares": "50", "orderstatus": "complete", "updatetime": now - 5},
    ]
    net, bad = position_reconciler.net_lots_by_symbol_from_orders(
        orders, LOT_SIZES, now_ts=now, min_age_seconds=60
    )
    assert net == {}  # too recent — excluded, not counted as a mismatch source
    assert bad == []


def test_net_lots_from_orders_flags_unresolvable_lot_size():
    orders = [
        {"tradingsymbol": "UNKNOWNSYM24OCT1CE", "transactiontype": "BUY",
         "filledshares": "10", "orderstatus": "complete", "updatetime": 100},
    ]
    net, bad = position_reconciler.net_lots_by_symbol_from_orders(
        orders, LOT_SIZES, now_ts=10_000.0
    )
    assert net == {}
    assert bad == ["UNKNOWNSYM24OCT1CE"]


def test_net_lots_from_positions_uses_symbolname_fallback():
    positions = [
        {"netqty": "50", "symbolname": "NIFTY24OCT23000CE"},
        {"netqty": "-30", "tradingsymbol": "BANKNIFTY24OCT48000PE"},
    ]
    net, bad = position_reconciler.net_lots_by_symbol_from_positions(positions, LOT_SIZES)
    assert net == {"NIFTY24OCT23000CE": 2, "BANKNIFTY24OCT48000PE": -2}
    assert bad == []


# ── reconcile() diff logic ───────────────────────────────────────────────

def test_reconcile_reports_no_mismatch_when_books_agree():
    now = 10_000.0
    orders = [
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "BUY",
         "filledshares": "50", "orderstatus": "complete", "updatetime": now - 200},
    ]
    positions = [{"netqty": "50", "tradingsymbol": "NIFTY24OCT23000CE"}]
    result = position_reconciler.reconcile(orders, positions, LOT_SIZES, now_ts=now)
    assert result.clean
    assert result.mismatches == []


def test_reconcile_flags_mismatch_between_order_book_and_positions():
    now = 10_000.0
    orders = [
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "BUY",
         "filledshares": "75", "orderstatus": "complete", "updatetime": now - 200},
    ]
    positions = [{"netqty": "50", "tradingsymbol": "NIFTY24OCT23000CE"}]  # only 2 lots, not 3
    result = position_reconciler.reconcile(orders, positions, LOT_SIZES, now_ts=now)
    assert not result.clean
    assert len(result.mismatches) == 1
    m = result.mismatches[0]
    assert m.symbol == "NIFTY24OCT23000CE"
    assert m.order_book_lots == 3
    assert m.position_lots == 2
    assert m.diff_lots == 1


def test_reconcile_flags_position_with_no_matching_orders():
    # Position exists (e.g. opened manually outside the app) but no
    # corresponding filled order in the order book.
    positions = [{"netqty": "15", "tradingsymbol": "BANKNIFTY24OCT48000PE"}]
    result = position_reconciler.reconcile([], positions, LOT_SIZES, now_ts=10_000.0)
    assert not result.clean
    assert result.mismatches[0].order_book_lots == 0
    assert result.mismatches[0].position_lots == 1


# ── PositionReconciler.check() — logging + kill-switch trip ─────────────

def test_check_does_not_trip_below_threshold(tmp_path, monkeypatch):
    pr, kill_switch_path = _guard(tmp_path, monkeypatch, trip_lots="2")
    reconciler = pr.PositionReconciler(kill_switch_path)

    now = 10_000.0
    orders = [
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "BUY",
         "filledshares": "50", "orderstatus": "complete", "updatetime": now - 200},
    ]
    positions = [{"netqty": "25", "tradingsymbol": "NIFTY24OCT23000CE"}]  # 1 lot off
    result = reconciler.check(orders, positions, LOT_SIZES, now_ts=now)

    assert len(result.mismatches) == 1
    assert not os.path.exists(kill_switch_path)


def test_check_trips_kill_switch_at_or_above_threshold(tmp_path, monkeypatch):
    pr, kill_switch_path = _guard(tmp_path, monkeypatch, trip_lots="2")
    reconciler = pr.PositionReconciler(kill_switch_path)

    now = 10_000.0
    orders = [
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "BUY",
         "filledshares": "100", "orderstatus": "complete", "updatetime": now - 200},
    ]
    positions = [{"netqty": "25", "tradingsymbol": "NIFTY24OCT23000CE"}]  # 3 lots off
    reconciler.check(orders, positions, LOT_SIZES, now_ts=now)

    assert os.path.exists(kill_switch_path)
    with open(kill_switch_path) as f:
        content = f.read()
    assert "position_reconciler" in content
    assert "NIFTY24OCT23000CE" in content


def test_check_clean_result_does_not_touch_kill_switch(tmp_path, monkeypatch):
    pr, kill_switch_path = _guard(tmp_path, monkeypatch, trip_lots="2")
    reconciler = pr.PositionReconciler(kill_switch_path)

    now = 10_000.0
    orders = [
        {"tradingsymbol": "NIFTY24OCT23000CE", "transactiontype": "BUY",
         "filledshares": "50", "orderstatus": "complete", "updatetime": now - 200},
    ]
    positions = [{"netqty": "50", "tradingsymbol": "NIFTY24OCT23000CE"}]
    result = reconciler.check(orders, positions, LOT_SIZES, now_ts=now)

    assert result.clean
    assert not os.path.exists(kill_switch_path)


def test_check_unparseable_symbol_does_not_trip_but_is_reported(tmp_path, monkeypatch):
    pr, kill_switch_path = _guard(tmp_path, monkeypatch, trip_lots="2")
    reconciler = pr.PositionReconciler(kill_switch_path)

    positions = [{"netqty": "10", "tradingsymbol": "UNKNOWNSYM24OCT1CE"}]
    result = reconciler.check([], positions, LOT_SIZES, now_ts=10_000.0)

    assert result.unparseable_symbols == ["UNKNOWNSYM24OCT1CE"]
    assert result.mismatches == []  # excluded, not flagged as a mismatch
    assert not os.path.exists(kill_switch_path)
