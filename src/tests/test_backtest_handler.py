"""Unit tests for ws_server_live.py's `backtest_handler` — the /api/backtest
HTTP endpoint the dashboard's backtest results viewer
(Dashboard/backtest-view.js) calls. Exercises the handler's own JSON
shaping (summary/trades/equityCurve) and query-param parsing; the actual
replay engine itself (backtest/replay.py's run_backtest) already has
dedicated coverage in test_backtest_replay.py, so `run_backtest` is
monkeypatched here to a fake that returns a canned BacktestResult,
same "don't re-test what's already tested elsewhere" posture as
test_handle_place_order.py stubbing the broker calls.
"""
import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


class _FakeRequest:
    """Minimal stand-in for aiohttp.web.Request — backtest_handler only
    ever touches request.query.get(...)."""
    def __init__(self, query=None):
        self.query = query or {}


def _closed_trade(symbol="NIFTY", pnl=100.0, exit_time="2026-07-01T10:05:00"):
    from backtest.replay import SimTrade
    t = SimTrade(
        symbol=symbol, expiry="31-Jul-2026", instrument_type="CE", side="BUY",
        strike=25000, qty_lots=1, lot_size=25,
        entry_time="2026-07-01T10:00:00", entry_price=100.0,
    )
    t.close(exit_time, 100.0 + (pnl / 25.0), "opposite_signal")
    return t


@pytest.fixture
def backtest_env(ws_server_live, monkeypatch):
    m = ws_server_live
    captured_kwargs = {}

    async def _fake_run_backtest(symbol, **kwargs):
        from backtest.replay import BacktestResult
        captured_kwargs["symbol"] = symbol
        captured_kwargs.update(kwargs)
        result = BacktestResult(symbol=symbol)
        result.trades = [_closed_trade(symbol, pnl=100.0), _closed_trade(symbol, pnl=-40.0)]
        result.unpriced_signals = 1
        return result

    monkeypatch.setattr(m, "run_backtest", _fake_run_backtest)
    return m, captured_kwargs


def test_defaults_to_server_symbol_when_none_given(backtest_env):
    m, captured = backtest_env
    monkeypatch_symbol = "BANKNIFTY"
    m.SYMBOL = monkeypatch_symbol

    resp = _run(m.backtest_handler(_FakeRequest()))

    assert resp.status == 200
    assert captured["symbol"] == monkeypatch_symbol


def test_explicit_symbol_query_param_overrides_server_symbol(backtest_env):
    m, captured = backtest_env
    m.SYMBOL = "NIFTY"

    _run(m.backtest_handler(_FakeRequest({"symbol": "banknifty"})))

    assert captured["symbol"] == "BANKNIFTY"  # upper-cased, same as history_handler's req_symbol handling


def test_query_params_parsed_and_forwarded(backtest_env):
    m, captured = backtest_env

    _run(m.backtest_handler(_FakeRequest({
        "symbol": "NIFTY", "start": "2026-07-01", "end": "2026-07-31",
        "minConfidence": "55", "cooldownSeconds": "120",
        "maxTradesPerSymbolPerDay": "3", "qtyLots": "2",
        "useAccountGuard": "true",
    })))

    assert captured["start"] == "2026-07-01"
    assert captured["end"] == "2026-07-31"
    assert captured["min_confidence"] == 55
    assert captured["cooldown_seconds"] == 120
    assert captured["max_trades_per_symbol_per_day"] == 3
    assert captured["qty_lots"] == 2
    assert captured["use_account_guard"] is True


def test_malformed_int_param_falls_back_to_default(backtest_env):
    m, captured = backtest_env

    _run(m.backtest_handler(_FakeRequest({"minConfidence": "not-a-number"})))

    assert captured["min_confidence"] == 40  # run_backtest's own default


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("True", True), ("yes", True),
    ("0", False), ("false", False), ("", False),
])
def test_use_account_guard_boolean_parsing(backtest_env, raw, expected):
    m, captured = backtest_env

    _run(m.backtest_handler(_FakeRequest({"useAccountGuard": raw})))

    assert captured["use_account_guard"] is expected


def test_response_shape_includes_summary_trades_and_equity_curve(backtest_env):
    m, _ = backtest_env

    resp = _run(m.backtest_handler(_FakeRequest({"symbol": "NIFTY"})))
    body = _json_body(resp)

    assert body["symbol"] == "NIFTY"
    assert body["summary"]["num_trades"] == 2
    assert body["summary"]["total_pnl"] == pytest.approx(60.0)
    assert body["summary"]["unpriced_signals"] == 1
    assert len(body["trades"]) == 2
    assert body["trades"][0]["side"] == "BUY"
    assert body["trades"][0]["pnl"] == pytest.approx(100.0)


def test_equity_curve_is_cumulative_over_closed_trades_in_order(backtest_env):
    m, _ = backtest_env

    resp = _run(m.backtest_handler(_FakeRequest({"symbol": "NIFTY"})))
    body = _json_body(resp)

    curve = body["equityCurve"]
    assert [p["seq"] for p in curve] == [1, 2]
    assert curve[0]["cumPnl"] == pytest.approx(100.0)
    assert curve[1]["cumPnl"] == pytest.approx(60.0)  # 100 + (-40)


def test_run_backtest_exception_returns_500_with_error_message(ws_server_live, monkeypatch):
    m = ws_server_live

    async def _boom(symbol, **kwargs):
        raise RuntimeError("no decision snapshots for this range")

    monkeypatch.setattr(m, "run_backtest", _boom)

    resp = _run(m.backtest_handler(_FakeRequest({"symbol": "NIFTY"})))

    assert resp.status == 500
    assert "no decision snapshots" in _json_body(resp)["error"]


def _json_body(resp):
    """aiohttp's web.json_response() stores its already-serialized body
    on .body / .text rather than exposing the dict back out — decode it
    the same way a real client hitting this endpoint would."""
    import json
    return json.loads(resp.text)
