"""Unit tests for paper_trading.py.

Includes explicit regression coverage for two bugs found and fixed during
code review:
  - unrealized_pnl was missing the lot_size multiplier (get_positions()).
  - _apply_fill_to_position()'s read-modify-write could race under
    concurrent place_order() calls on the same instrument.
"""

import threading

import pytest

from paper_trading import PaperTradingEngine, _instrument_key


@pytest.fixture
def engine():
    return PaperTradingEngine(db_path=":memory:")


def test_market_buy_fills_immediately(engine):
    order = engine.place_order(
        "NIFTY", "BUY", qty_lots=2, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000,
        order_type="MARKET", current_ltp=120.5,
        enforce_risk_checks=False,
    )
    assert order.status == "FILLED"
    assert order.fill_price == 120.5
    assert order.price_source == "server_live_tick"
    assert order.slippage_assumption == "none"
    assert order.fill_delay_ms is not None


def test_equity_quantity_is_shares_not_futures_lots(engine):
    engine.place_order(
        "NIFTY", "BUY", qty_lots=2, instrument_type="INDEX",
        order_type="MARKET", current_ltp=100.0,
        enforce_risk_checks=False,
    )
    key = _instrument_key("NIFTY", "", None, "INDEX")
    positions = engine.get_positions({key: 110.0})
    assert positions[0]["unrealized_pnl"] == pytest.approx((110.0 - 100.0) * 2)


def test_client_order_id_makes_submission_idempotent(engine):
    kwargs = dict(
        symbol="NIFTY", side="BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000, order_type="MARKET",
        current_ltp=100.0, enforce_risk_checks=False,
        client_order_id="browser-submit-123",
    )
    first = engine.place_order(**kwargs)
    second = engine.place_order(**kwargs)

    assert second.id == first.id
    assert len(engine.get_orders()) == 1
    assert engine.get_positions()[0]["net_qty_lots"] == 1


def test_rejected_retry_is_also_idempotent(engine):
    first = engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000, order_type="MARKET",
        current_ltp=None, client_order_id="missing-price-123")
    second = engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000, order_type="MARKET",
        current_ltp=None, client_order_id="missing-price-123")

    assert first.status == "REJECTED"
    assert second.id == first.id
    assert len(engine.get_orders()) == 1


def test_unsupported_order_type_is_explicitly_rejected(engine):
    order = engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000, order_type="SL-M",
        current_ltp=100.0, client_order_id="unsupported-123")
    assert order.status == "REJECTED"
    assert "unsupported" in order.reject_reason.lower()


def test_fund_summary_reconciles_equity_and_open_pnl(engine):
    engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000, order_type="MARKET",
        current_ltp=100.0, enforce_risk_checks=False)
    key = _instrument_key("NIFTY", "31-Jul-2026", 25000, "CE")
    summary = engine.get_fund_summary(current_prices={key: 110.0})

    assert summary["equity"] == pytest.approx(summary["capital"] + summary["realized_pnl"] + summary["unrealized_pnl"])
    assert summary["fund"] == pytest.approx(summary["equity"] - summary["margin_blocked"])


def test_limit_order_stays_pending_until_price_crosses(engine):
    order = engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000,
        order_type="LIMIT", limit_price=100.0, current_ltp=120.5,
        enforce_risk_checks=False,
    )
    assert order.status == "PENDING"

    key = _instrument_key("NIFTY", "31-Jul-2026", 25000, "CE")
    engine.check_pending_orders({key: 110.0})  # hasn't crossed yet
    assert engine.get_orders(status="PENDING")

    engine.check_pending_orders({key: 95.0})  # now crosses (<= limit)
    assert not engine.get_orders(status="PENDING")
    assert engine.get_orders(status="FILLED")


def test_unrealized_pnl_includes_lot_size(engine):
    """Regression test: unrealized_pnl must scale by lot_size, matching
    the realized-P&L formula used elsewhere in this module (previously it
    did not, understating P&L by the lot-size factor)."""
    engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000,
        order_type="MARKET", current_ltp=100.0,
        enforce_risk_checks=False,
    )
    key = _instrument_key("NIFTY", "31-Jul-2026", 25000, "CE")
    positions = engine.get_positions({key: 110.0})
    lot_size = 65  # static NIFTY fallback used when the live master is unreachable

    assert positions[0]["unrealized_pnl"] == pytest.approx((110.0 - 100.0) * 1 * lot_size)


def test_realized_pnl_on_partial_close(engine):
    engine.place_order(
        "NIFTY", "BUY", qty_lots=2, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000,
        order_type="MARKET", current_ltp=100.0,
        enforce_risk_checks=False,
    )
    engine.place_order(
        "NIFTY", "SELL", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000,
        order_type="MARKET", current_ltp=120.0,
        enforce_risk_checks=False,
    )
    key = _instrument_key("NIFTY", "31-Jul-2026", 25000, "CE")
    positions = engine.get_positions({key: 120.0})
    lot_size = 65
    assert positions[0]["net_qty_lots"] == 1
    assert positions[0]["realized_pnl"] == pytest.approx((120.0 - 100.0) * 1 * lot_size)


def test_price_band_rejects_far_limit_price(engine):
    order = engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000,
        order_type="LIMIT", limit_price=200.0, current_ltp=120.5,
    )
    assert order.status == "REJECTED"
    assert "band" in order.reject_reason.lower()


def test_margin_check_rejects_oversized_order(engine):
    order = engine.place_order(
        "BANKNIFTY", "SELL", qty_lots=50, instrument_type="PE",
        expiry="31-Jul-2026", strike=52000,
        order_type="MARKET", current_ltp=300.0,
        spot_price=52000.0, account_capital=100_000.0,
    )
    assert order.status == "REJECTED"
    assert "margin" in order.reject_reason.lower()


def test_limit_order_fills_and_merges_with_existing_market_position(engine):
    """Regression test: _instrument_key() used to interpolate `strike`
    without normalizing type. SQLite always returns a REAL column as a
    float, but a freshly-placed order (still in memory) kept whatever
    type the caller passed — usually a plain int. Since check_pending_orders()
    always rebuilds its Order objects from a DB row, a LIMIT order's key
    would come out as ".../25000.0/CE" while a MARKET order on the exact
    same strike, still in memory, keyed itself ".../25000/CE" — two
    different strings for one instrument. In practice this meant a
    current_prices dict built the natural way (int strikes) could never
    match a pending LIMIT order, which would then sit PENDING forever
    instead of ever filling."""
    engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000,
        order_type="MARKET", current_ltp=100.0,
        enforce_risk_checks=False,
    )
    engine.place_order(
        "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
        expiry="31-Jul-2026", strike=25000,
        order_type="LIMIT", limit_price=105.0, current_ltp=100.0,
        enforce_risk_checks=False,
    )
    key = _instrument_key("NIFTY", "31-Jul-2026", 25000, "CE")  # int strike
    engine.check_pending_orders({key: 100.0})

    assert not engine.get_orders(status="PENDING")
    positions = engine.get_positions()
    assert len(positions) == 1
    assert positions[0]["net_qty_lots"] == 2


def test_concurrent_fills_on_same_instrument_do_not_race(engine):
    """Regression test for the position-update race condition: 20 threads
    each buying 1 lot concurrently must land on exactly net_qty_lots=20,
    not less (which would indicate a lost update)."""
    def worker():
        engine.place_order(
            "NIFTY", "BUY", qty_lots=1, instrument_type="CE",
            expiry="31-Jul-2026", strike=25000,
            order_type="MARKET", current_ltp=120.5,
            enforce_risk_checks=False,
        )

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    positions = engine.get_positions()
    assert positions[0]["net_qty_lots"] == 20
