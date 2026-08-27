"""Unit tests for server/app.py's `_handle_place_order` — the single
chokepoint every order (manual dashboard click or AutoExecutor-submitted)
passes through before it can reach a real AngelOne order or the paper
trading engine.

Nothing here talks to a real broker: each test installs a fresh
``LiveOrderGateway`` with injected placement, position, and resolver fakes.
See conftest.py's
`ws_server_live` fixture for how the module is made importable at all
without a network call.
"""
import asyncio
import time

import pytest

from server.order_gateway import LiveOrderGateway


class _FakeGuard:
    """Stand-in for risk.account_guard.LiveAccountRiskGuard — the guard's
    own trip/exposure logic already has dedicated coverage in
    test_account_guard.py, so most tests here just need a controllable
    is_tripped()/check_new_order() rather than a real sqlite-backed one."""

    def __init__(self, tripped=False, trip_reason=None, allow_new_order=True, exposure_reason=None):
        self.tripped = tripped
        self.trip_reason = trip_reason
        self.allow_new_order = allow_new_order
        self.exposure_reason = exposure_reason
        self.update_pnl_calls = []

    def is_tripped(self):
        return self.tripped, self.trip_reason

    def check_new_order(self, qty_lots, current_open_lots):
        return self.allow_new_order, self.exposure_reason

    def update_pnl(self, pnl):
        self.update_pnl_calls.append(pnl)


class _StubFilledOrder:
    status = "FILLED"
    fill_price = 100.0
    reject_reason = None


class _MemoryLiveOrderStore:
    def __init__(self):
        self.orders = {}

    def get(self, client_order_id):
        return self.orders.get(client_order_id)

    def record(self, client_order_id, broker_order_id):
        self.orders.setdefault(client_order_id, str(broker_order_id))
        return self.orders[client_order_id]


def _order_payload(**overrides):
    payload = {
        "symbol": "NIFTY",
        "instrument_type": "CE",
        "expiry": "31-Jul-2026",
        "strike": 25000,
        "side": "SELL",
        "order_type": "MARKET",
        "qty_lots": 1,
        "client_order_id": "liveorder00000001",
        "live": True,
        "confirmed": True,
    }
    payload.update(overrides)
    return payload


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def live_env(ws_server_live, monkeypatch, tmp_path):
    """Base wiring shared by every test below: live trading toggled on,
    kill switch pointed at a scratch path (absent by default), rate-limit
    window cleared, a permissive fake guard, and a clean empty position
    book. Tests that need to reach placement layer resolver and broker fakes
    through the `resolvable` fixture."""
    m = ws_server_live
    # PT_LOT_SIZES (lot_sizes.LOT_SIZES) is a dict subclass that only
    # resolves a symbol's lot size lazily, via __missing__ — which
    # `.get()`/`[]` trigger but plain `in` does NOT. In real production
    # this is harmless: option_chain_json.py's engine_loop already calls
    # `LOT_SIZES.get(SYMBOL, 65)` every tick for whatever symbol the
    # dashboard is actively showing, long before a live order for that
    # symbol could be clicked, so the entry always exists by the time
    # `_handle_place_order`'s `symbol not in PT_LOT_SIZES` check runs.
    # Nothing in this isolated test process ever runs that pipeline, so
    # tests reaching this check need to warm it the same way — otherwise
    # every single test would spuriously hit "no verified lot size",
    # which is a misleading rejection reason for what's actually just an
    # unwarmed cache.
    m.PT_LOT_SIZES.get("NIFTY")
    gateway = LiveOrderGateway(
        enabled=True,
        kill_switch_file=str(tmp_path / "LIVE_TRADING_KILL"),
        max_lots_per_order=5,
        max_orders_per_minute=5,
        lot_sizes=m.PT_LOT_SIZES,
        account_guard=_FakeGuard(),
        position_reconciler=m._POSITION_RECONCILER,
        resolve_token=m._resolve_live_order_token,
        place_order=lambda *_args, **_kwargs: "UNEXPECTED",
        get_positions=lambda: [],
        get_order_book=lambda: [],
        order_store=_MemoryLiveOrderStore(),
    )
    monkeypatch.setattr(m, "_LIVE_ORDERS", gateway)
    return m


@pytest.fixture
def resolvable(live_env, monkeypatch):
    """Add resolver and placement fakes to the fresh live-order gateway."""
    m = live_env
    monkeypatch.setattr(
        m._LIVE_ORDERS, "_resolve_token",
        lambda symbol, instrument_type, expiry, strike: ("NFO", "NIFTY31JUL25000CE", "999999"),
    )
    placed = {}

    def _fake_place_order(*args, **kwargs):
        placed["args"] = args
        placed["kwargs"] = kwargs
        return "ORDER123"

    monkeypatch.setattr(m._LIVE_ORDERS, "_place_order", _fake_place_order)
    return m, placed


# ── Rejection chain — each of these must fall through WITHOUT ever
#    calling smartapi_place_order ────────────────────────────────────────

def test_live_trading_disabled_rejects_explicit_live_order_without_paper_fallback(live_env, monkeypatch):
    """An order that explicitly asked for live=true, confirmed=true is a
    deliberate request for a REAL fill — if the server can't honor that
    (LIVE_TRADING_ENABLED=false here), it must be rejected outright, not
    silently filled on paper instead. Silently falling back would show
    the client a "filled" order that never touched the real account,
    which is worse than an explicit rejection. (Falling through to paper
    is reserved for the *other* case — the client never asked for live
    in the first place; see test_anything_short_of_live_and_confirmed_uses_paper_engine.)"""
    m = live_env
    monkeypatch.setattr(m._LIVE_ORDERS, "enabled", False)
    paper_calls = {}
    monkeypatch.setattr(
        m.PT_ENGINE, "place_order",
        lambda *a, **k: (paper_calls.setdefault("called", True), _StubFilledOrder())[1],
    )
    broker_calls = {}
    monkeypatch.setattr(m._LIVE_ORDERS, "_place_order", lambda *a, **k: broker_calls.setdefault("called", True))
    _run(m._handle_place_order(_order_payload()))
    assert "called" not in broker_calls
    assert "called" not in paper_calls


def test_kill_switch_active_rejects(resolvable):
    m, placed = resolvable
    with open(m._LIVE_ORDERS.kill_switch_file, "w") as f:
        f.write("stop")
    _run(m._handle_place_order(_order_payload()))
    assert "args" not in placed


def test_qty_lots_zero_rejects(resolvable):
    m, placed = resolvable
    _run(m._handle_place_order(_order_payload(qty_lots=0)))
    assert "args" not in placed


@pytest.mark.parametrize("side", [None, "", "HOLD"])
def test_invalid_side_is_rejected_before_broker_or_paper(live_env, monkeypatch, side):
    m = live_env
    broker_calls = {}
    paper_calls = {}
    monkeypatch.setattr(m._LIVE_ORDERS, "_place_order", lambda *a, **k: broker_calls.setdefault("called", True))
    monkeypatch.setattr(m.PT_ENGINE, "place_order", lambda *a, **k: paper_calls.setdefault("called", True))

    result = _run(m._handle_place_order(_order_payload(side=side)))

    assert result["status"] == "rejected"
    assert "side" in result["reason"]
    assert "called" not in broker_calls
    assert "called" not in paper_calls


@pytest.mark.parametrize("overrides", [
    {"order_type": "LIMIT", "limit_price": 0},
    {"order_type": "LIMIT", "limit_price": "NaN"},
    {"order_type": "SL-M"},
    {"instrument_type": "CE", "strike": None},
    {"instrument_type": "FUT", "expiry": ""},
    {"qty_lots": 1.5},
])
def test_malformed_order_intent_never_reaches_broker(resolvable, overrides):
    m, placed = resolvable
    result = _run(m._handle_place_order(_order_payload(**overrides)))
    assert result["status"] == "rejected"
    assert "args" not in placed


def test_qty_lots_over_max_rejects(resolvable, monkeypatch):
    m, placed = resolvable
    monkeypatch.setattr(m._LIVE_ORDERS, "max_lots_per_order", 1)
    _run(m._handle_place_order(_order_payload(qty_lots=2)))
    assert "args" not in placed


def test_rate_limit_exceeded_rejects_second_order(resolvable, monkeypatch):
    m, placed = resolvable
    monkeypatch.setattr(m._LIVE_ORDERS, "max_orders_per_minute", 1)
    _run(m._handle_place_order(_order_payload(client_order_id="liveorder00000001")))
    assert "args" in placed, "first order should still go through and consume the window's one slot"
    placed.clear()
    _run(m._handle_place_order(_order_payload(client_order_id="liveorder00000002")))
    assert "args" not in placed, "second order within the same window should be rejected pre-broker"


@pytest.mark.parametrize("client_order_id", [None, "short", "live_order_000001", "x" * 21])
def test_live_order_requires_bounded_alphanumeric_client_identity(resolvable, client_order_id):
    m, placed = resolvable
    result = _run(m._handle_place_order(_order_payload(client_order_id=client_order_id)))
    assert result["status"] == "rejected"
    assert "client_order_id" in result["reason"]
    assert "args" not in placed


def test_replayed_live_order_returns_original_without_second_broker_call(resolvable):
    m, placed = resolvable
    first = _run(m._handle_place_order(_order_payload()))
    first_args = placed["args"]
    first_kwargs = placed["kwargs"]
    placed.clear()

    replay = _run(m._handle_place_order(_order_payload()))

    assert first["order_id"] == replay["order_id"] == "ORDER123"
    assert replay["duplicate"] is True
    assert "args" not in placed
    assert first_args
    assert first_kwargs["order_tag"] == "liveorder00000001"


def test_unknown_symbol_rejects_without_guessing_lot_size(resolvable):
    m, placed = resolvable
    _run(m._handle_place_order(_order_payload(symbol="NOTAREALSYMBOL")))
    assert "args" not in placed


def test_account_guard_tripped_rejects(resolvable, monkeypatch):
    m, placed = resolvable
    monkeypatch.setattr(m._LIVE_ORDERS, "_guard", _FakeGuard(tripped=True, trip_reason="daily loss limit breached"))
    _run(m._handle_place_order(_order_payload()))
    assert "args" not in placed


def test_exposure_check_failure_rejects(resolvable, monkeypatch):
    m, placed = resolvable
    monkeypatch.setattr(
        m._LIVE_ORDERS, "_guard",
        _FakeGuard(allow_new_order=False, exposure_reason="would exceed max open exposure"),
    )
    _run(m._handle_place_order(_order_payload()))
    assert "args" not in placed


def test_position_book_fetch_failure_still_consults_guard_with_none(resolvable, monkeypatch):
    """If the pre-trade position-book fetch itself raises,
    open_lots_from_positions can't be computed — _handle_place_order must
    pass None through to the guard (fail-closed territory, exercised in
    test_account_guard.py) rather than treating the failure as "no open
    positions, go ahead"."""
    m, placed = resolvable

    def _boom():
        raise RuntimeError("network blip")

    monkeypatch.setattr(m._LIVE_ORDERS, "_get_positions", _boom)
    seen = {}
    guard = _FakeGuard(allow_new_order=False, exposure_reason="could not verify current open exposure")
    monkeypatch.setattr(guard, "check_new_order", lambda qty, open_lots: seen.setdefault("open_lots", open_lots) or (False, "could not verify current open exposure"))
    monkeypatch.setattr(m._LIVE_ORDERS, "_guard", guard)
    _run(m._handle_place_order(_order_payload()))
    assert "args" not in placed
    assert seen["open_lots"] is None


def test_instrument_resolution_failure_rejects(live_env, monkeypatch):
    """INDEX isn't a tradeable instrument on its own — exercised against
    the REAL _resolve_live_order_token (live_env doesn't stub it), so
    this covers that function's own refusal, not just a mock standing in
    for it."""
    m = live_env
    calls = {}
    monkeypatch.setattr(m._LIVE_ORDERS, "_place_order", lambda *a, **k: calls.setdefault("called", True))
    _run(m._handle_place_order(_order_payload(instrument_type="INDEX")))
    assert "called" not in calls


# ── Placement itself ─────────────────────────────────────────────────────

def test_successful_live_order_places_with_resolved_token_and_correct_quantity(resolvable):
    m, placed = resolvable
    lot_size = m.PT_LOT_SIZES["NIFTY"]
    _run(m._handle_place_order(_order_payload(qty_lots=2, side="SELL")))
    assert placed["args"][0] == "NIFTY31JUL25000CE"   # tradingsymbol
    assert placed["args"][2] == "NFO"                  # exchange
    assert placed["args"][3] == "SELL"                 # transaction_type
    assert placed["args"][4] == 2 * lot_size           # quantity = qty_lots * lot_size


def test_successful_live_order_updates_guard_pnl_from_post_fill_positions(resolvable, monkeypatch):
    m, placed = resolvable
    guard = _FakeGuard()
    monkeypatch.setattr(m._LIVE_ORDERS, "_guard", guard)
    monkeypatch.setattr(m._LIVE_ORDERS, "_get_positions", lambda: [{"netqty": "0", "pnl": "250.0"}])
    _run(m._handle_place_order(_order_payload()))
    assert "args" in placed
    assert guard.update_pnl_calls == [250.0]


def test_broker_exception_during_placement_is_caught_not_raised(resolvable, monkeypatch):
    m, placed = resolvable

    def _raise(*a, **k):
        raise RuntimeError("AngelOne rejected: margin insufficient")

    monkeypatch.setattr(m._LIVE_ORDERS, "_place_order", _raise)
    guard = _FakeGuard()
    monkeypatch.setattr(m._LIVE_ORDERS, "_guard", guard)
    # Should not raise, and should still attempt the post-fill P&L refresh.
    _run(m._handle_place_order(_order_payload()))
    assert guard.update_pnl_calls == [0.0]  # empty position book -> pnl_from_positions([]) == 0.0


def test_buy_and_sell_sides_map_to_correct_transaction_type(resolvable):
    m, placed = resolvable
    _run(m._handle_place_order(_order_payload(side="buy", client_order_id="liveorder00000001")))
    assert placed["args"][3] == "BUY"
    placed.clear()
    _run(m._handle_place_order(_order_payload(side="sell", client_order_id="liveorder00000002")))
    assert placed["args"][3] == "SELL"


def test_closing_order_checks_projected_reduced_exposure(resolvable, monkeypatch):
    m, placed = resolvable
    lot_size = m.PT_LOT_SIZES["NIFTY"]
    monkeypatch.setattr(m._LIVE_ORDERS, "_get_positions", lambda: [{
        "netqty": str(lot_size),
        "tradingsymbol": "NIFTY31JUL25000CE",
        "pnl": "0",
    }])
    seen = {}
    guard = _FakeGuard()
    monkeypatch.setattr(
        guard, "check_new_order",
        lambda qty, projected: (seen.setdefault("projected", projected), (True, None))[1],
    )
    monkeypatch.setattr(m._LIVE_ORDERS, "_guard", guard)

    result = _run(m._handle_place_order(_order_payload(side="SELL")))

    assert result["status"] == "placed"
    assert seen["projected"] == 0
    assert "args" in placed


def test_concurrent_orders_cannot_race_exposure_cap(resolvable, monkeypatch):
    m, _ = resolvable
    lot_size = m.PT_LOT_SIZES["NIFTY"]
    positions = []
    broker_calls = []

    def _positions():
        # Ensure the first pre-trade read yields long enough that an
        # unguarded second coroutine would observe the same empty book.
        if not positions:
            time.sleep(0.05)
        return [dict(row) for row in positions]

    def _place(*args, **kwargs):
        broker_calls.append(args)
        positions[:] = [{
            "netqty": str(lot_size),
            "tradingsymbol": "NIFTY31JUL25000CE",
            "pnl": "0",
        }]
        return f"ORDER{len(broker_calls)}"

    guard = _FakeGuard()
    monkeypatch.setattr(
        guard, "check_new_order",
        lambda qty, projected: (
            (projected is not None and projected <= 1),
            None if projected is not None and projected <= 1 else "would exceed max open exposure",
        ),
    )
    monkeypatch.setattr(m._LIVE_ORDERS, "_guard", guard)
    monkeypatch.setattr(m._LIVE_ORDERS, "_get_positions", _positions)
    monkeypatch.setattr(m._LIVE_ORDERS, "_place_order", _place)

    async def _concurrent():
        return await asyncio.gather(
            m._handle_place_order(_order_payload(side="BUY", client_order_id="liveorder00000011")),
            m._handle_place_order(_order_payload(side="BUY", client_order_id="liveorder00000012")),
        )

    results = _run(_concurrent())
    assert len(broker_calls) == 1
    assert sorted(result["status"] for result in results) == ["placed", "rejected"]


# ── Paper trading path (live=False or confirmed=False) ──────────────────

@pytest.mark.parametrize("overrides", [{"live": False, "confirmed": True}, {"live": True, "confirmed": False}, {}])
def test_anything_short_of_live_and_confirmed_uses_paper_engine(resolvable, monkeypatch, overrides):
    m, placed = resolvable
    calls = {}
    monkeypatch.setattr(
        m.PT_ENGINE, "place_order",
        lambda *a, **k: (calls.setdefault("called", True), _StubFilledOrder())[1],
    )
    payload = _order_payload(**{**{"live": False, "confirmed": False}, **overrides})
    _run(m._handle_place_order(payload))
    assert "args" not in placed, "must never reach the real broker without explicit live+confirmed"
    assert calls.get("called") is True
