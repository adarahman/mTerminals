"""Unit tests for ws_server_live.py's `_broadcast_reconciliation_alert` —
turns a risk/position_reconciler.py `ReconciliationResult` into the
{"type":"reconciliationAlert",...} broadcast the dashboard's toast/banner
(algo-status.js's renderReconciliationAlerts) consumes. Before this
existed, a non-clean check() result (even one well below the kill-switch
trip threshold) was only ever printed to the server log — see
reconcile_loop's and _handle_place_order's own prints.

Nothing here talks to a real broker or a real websocket: `broadcast` is
monkeypatched to just record what it was called with, same pattern
test_handle_place_order.py uses for the broker calls it stubs.
"""
import asyncio

import pytest

from risk.position_reconciler import Mismatch, ReconciliationResult


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def recon_env(ws_server_live, monkeypatch):
    """Captures every broadcast() call instead of touching CONNECTED/real
    websockets, and gives each test a fresh LAST_RECONCILIATION_ALERT /
    trip_lots so tests don't leak state into each other (module-level
    globals, same reason live_env resets _live_order_timestamps etc. in
    test_handle_place_order.py)."""
    m = ws_server_live
    sent = []

    async def _fake_broadcast(message):
        sent.append(message)

    monkeypatch.setattr(m, "broadcast", _fake_broadcast)
    monkeypatch.setattr(m, "LAST_RECONCILIATION_ALERT", None)
    monkeypatch.setattr(m._POSITION_RECONCILER, "trip_lots", 2)
    return m, sent


def test_clean_result_does_not_broadcast(recon_env):
    m, sent = recon_env
    result = ReconciliationResult(mismatches=[], unparseable_symbols=[])

    _run(m._broadcast_reconciliation_alert(result, source="periodic"))

    assert sent == []
    assert m.LAST_RECONCILIATION_ALERT is None


def test_below_threshold_mismatch_broadcasts_untripped(recon_env):
    m, sent = recon_env
    result = ReconciliationResult(
        mismatches=[Mismatch(symbol="NIFTY24OCT23000CE", order_book_lots=2, position_lots=1)],
        unparseable_symbols=[],
    )

    _run(m._broadcast_reconciliation_alert(result, source="periodic"))

    assert len(sent) == 1
    msg = sent[0]
    assert msg["type"] == "reconciliationAlert"
    payload = msg["payload"]
    assert payload["tripped"] is False
    assert payload["source"] == "periodic"
    assert payload["tripLots"] == 2
    assert payload["mismatches"] == [
        {"symbol": "NIFTY24OCT23000CE", "orderBookLots": 2, "positionLots": 1, "diffLots": 1}
    ]
    assert payload["unparseableSymbols"] == []
    assert "ts" in payload
    # Broadcasting also updates the snapshot new connections are handed.
    assert m.LAST_RECONCILIATION_ALERT == payload


def test_at_or_above_threshold_mismatch_marks_tripped(recon_env):
    m, sent = recon_env
    result = ReconciliationResult(
        mismatches=[Mismatch(symbol="BANKNIFTY24OCT48000PE", order_book_lots=5, position_lots=2)],
        unparseable_symbols=[],
    )

    _run(m._broadcast_reconciliation_alert(result, source="post_fill"))

    payload = sent[0]["payload"]
    assert payload["tripped"] is True
    assert payload["mismatches"][0]["diffLots"] == 3
    assert payload["source"] == "post_fill"


def test_unparseable_symbols_alone_still_broadcasts(recon_env):
    """A result can be non-clean purely because of unparseable rows, with
    zero actual mismatches — reconcile()'s `clean` property already
    treats that as non-clean, this just confirms the broadcast path
    doesn't assume `mismatches` is non-empty."""
    m, sent = recon_env
    result = ReconciliationResult(mismatches=[], unparseable_symbols=["WEIRDSYM"])

    _run(m._broadcast_reconciliation_alert(result, source="periodic"))

    assert len(sent) == 1
    payload = sent[0]["payload"]
    assert payload["mismatches"] == []
    assert payload["unparseableSymbols"] == ["WEIRDSYM"]
    # max_abs_diff_lots() is 0 with no mismatches, so this never counts as
    # tripped purely from unparseable rows — only an actual lot-diff can
    # trip the kill switch (see position_reconciler.py's PositionReconciler.check).
    assert payload["tripped"] is False


def test_multiple_mismatches_all_included(recon_env):
    m, sent = recon_env
    result = ReconciliationResult(
        mismatches=[
            Mismatch(symbol="NIFTY24OCT23000CE", order_book_lots=1, position_lots=0),
            Mismatch(symbol="NIFTY24OCT23500PE", order_book_lots=0, position_lots=1),
        ],
        unparseable_symbols=[],
    )

    _run(m._broadcast_reconciliation_alert(result, source="periodic"))

    payload = sent[0]["payload"]
    assert {mm["symbol"] for mm in payload["mismatches"]} == {
        "NIFTY24OCT23000CE", "NIFTY24OCT23500PE",
    }


def test_last_reconciliation_alert_replayed_to_new_connections_field_shape(recon_env):
    """Not exercising the actual aiohttp handshake here (out of scope for
    a unit test), just confirming the module-level snapshot the
    websocket handler's initial-send branch serializes is the exact same
    dict that was broadcast — i.e. broadcasting and "what a new client is
    handed" never drift apart because they're the same object."""
    m, sent = recon_env
    result = ReconciliationResult(
        mismatches=[Mismatch(symbol="NIFTY24OCT23000CE", order_book_lots=1, position_lots=0)],
        unparseable_symbols=[],
    )

    _run(m._broadcast_reconciliation_alert(result, source="periodic"))

    assert m.LAST_RECONCILIATION_ALERT is sent[0]["payload"]
