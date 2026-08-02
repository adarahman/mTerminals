"""Unit tests for brokers/smartapi_ws_client.py's SmartTickStream — the
WebSocket reconnect layer that run_forever_with_reconnect() and
_handle_open()'s subscription-replay exist to make unattended.

These never open a real WebSocket: SmartWebSocketV2 is monkeypatched to
a MagicMock class, and the shared brokers.smartapi_client._session
singleton has its private auth/feed-token/session-object fields poked
directly so connect() doesn't try to actually log in.
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def stream(smartapi_modules, monkeypatch):
    smartapi_client, smartapi_ws_client = smartapi_modules

    # Make the shared session look already-logged-in so connect() doesn't
    # try to reach _login()/the real network for auth/feed tokens.
    session = smartapi_client._session
    monkeypatch.setattr(session, "_smart_api", MagicMock())
    monkeypatch.setattr(session, "_auth_token", "fake-auth-token")
    monkeypatch.setattr(session, "_feed_token", "fake-feed-token")
    monkeypatch.setattr(session, "_login_time", datetime.now())

    fake_ws_cls = MagicMock(side_effect=lambda *a, **k: MagicMock())
    monkeypatch.setattr(smartapi_ws_client, "SmartWebSocketV2", fake_ws_cls)

    s = smartapi_ws_client.SmartTickStream()
    s._fake_ws_cls = fake_ws_cls
    s._module = smartapi_ws_client  # stashed so tests can patch its `time.sleep`
    return s


# ── connect() wiring ─────────────────────────────────────────────────────

def test_connect_builds_ws_with_fresh_tokens_and_wires_callbacks(stream):
    stream.connect()
    stream._fake_ws_cls.assert_called_once()
    args, kwargs = stream._fake_ws_cls.call_args
    assert args[0] == "fake-auth-token"
    assert args[3] == "fake-feed-token"
    assert kwargs["max_retry_attempt"] == 5
    assert kwargs["retry_strategy"] == 1

    assert stream._ws.on_open == stream._handle_open
    assert stream._ws.on_data == stream._handle_data
    assert stream._ws.on_error == stream._handle_error
    assert stream._ws.on_close == stream._handle_close


# ── desired-subscription tracking & replay on reconnect ─────────────────

def test_subscribe_before_connect_only_stores_desired_state(stream):
    stream.subscribe("NFO", ["111"])
    assert stream._desired == {2: {"111"}}


def test_subscribe_while_connected_calls_do_subscribe_immediately(stream):
    stream.connect()
    stream._connected.set()
    stream.subscribe("NFO", ["333"])
    stream._ws.subscribe.assert_called_once_with(
        "mterminals", stream.mode, [{"exchangeType": 2, "tokens": ["333"]}]
    )


def test_handle_open_replays_desired_subscriptions(stream):
    stream.connect()
    stream.subscribe("NFO", ["111"])  # stored only, not yet connected
    stream._ws.subscribe.assert_not_called()

    stream._handle_open(stream._ws)

    assert stream._connected.is_set()
    stream._ws.subscribe.assert_called_once_with(
        "mterminals", stream.mode, [{"exchangeType": 2, "tokens": ["111"]}]
    )


def test_unsubscribe_removes_token_from_desired_state(stream):
    stream.subscribe("NFO", ["111", "222"])
    stream.unsubscribe("NFO", ["111"])
    assert stream._desired == {2: {"222"}}


# ── error/close callback gating around intentional close ────────────────

def test_handle_error_calls_callback_when_not_closing(stream):
    cb = MagicMock()
    stream._on_error_cb = cb
    stream._handle_error(None, "boom")
    cb.assert_called_once_with("boom")


def test_handle_error_swallowed_during_intentional_close(stream):
    cb = MagicMock()
    stream._on_error_cb = cb
    stream._closing = True
    stream._handle_error(None, "boom")
    cb.assert_not_called()


def test_handle_close_clears_connected_and_calls_callback(stream):
    stream._connected.set()
    cb = MagicMock()
    stream._on_close_cb = cb
    stream._handle_close(None)
    assert not stream._connected.is_set()
    cb.assert_called_once()


def test_handle_close_no_callback_during_intentional_close(stream):
    stream._closing = True
    cb = MagicMock()
    stream._on_close_cb = cb
    stream._handle_close(None)
    cb.assert_not_called()


# ── tick normalization ───────────────────────────────────────────────────

def test_normalize_tick_converts_paisa_price_fields_to_rupees(stream):
    tick = {"last_traded_price": 12345, "some_other_field": "unchanged"}
    out = stream._normalize_tick(tick)
    assert out["last_traded_price"] == 123.45
    assert out["some_other_field"] == "unchanged"


def test_normalize_tick_ignores_non_dict_payload(stream):
    assert stream._normalize_tick("not-a-dict") == "not-a-dict"


# ── run_forever_with_reconnect: the actual resilience loop ──────────────

def test_reconnect_loop_backs_off_and_rebuilds_after_one_disconnect(stream, monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(
        stream._module.time,
        "sleep",
        lambda s: sleep_calls.append(s),
    )

    connect_count = {"n": 0}
    stream._ws = MagicMock()

    def fake_ws_connect():
        connect_count["n"] += 1
        if connect_count["n"] >= 2:
            stream._closing = True  # stop the outer loop after the 2nd drop

    stream._ws.connect.side_effect = fake_ws_connect

    rebuild_calls = {"n": 0}
    monkeypatch.setattr(stream, "connect", lambda: rebuild_calls.__setitem__("n", rebuild_calls["n"] + 1))

    stream.run_forever_with_reconnect(initial_backoff=1, max_backoff=10)

    assert connect_count["n"] == 2
    assert rebuild_calls["n"] == 1
    assert sleep_calls == [1]


def test_reconnect_loop_backoff_doubles_across_rebuild_failures(stream, monkeypatch):
    """Backoff only escalates while *rebuilds* keep failing (the
    try/except/continue path) — a rebuild that succeeds resets it back
    to initial_backoff via the loop's own else-branch. This matters
    because otherwise a single flaky reconnect would keep the wait time
    inflated long after the connection recovered."""
    sleep_calls = []
    monkeypatch.setattr(
        stream._module.time,
        "sleep",
        lambda s: sleep_calls.append(s),
    )

    connect_count = {"n": 0}
    stream._ws = MagicMock()

    def fake_ws_connect():
        connect_count["n"] += 1
        if connect_count["n"] >= 4:
            stream._closing = True  # stop after the 4th drop

    stream._ws.connect.side_effect = fake_ws_connect

    rebuild_calls = {"n": 0}

    def flaky_connect():
        rebuild_calls["n"] += 1
        if rebuild_calls["n"] <= 2:
            raise RuntimeError("rebuild still failing")
        # 3rd rebuild attempt succeeds

    monkeypatch.setattr(stream, "connect", flaky_connect)

    stream.run_forever_with_reconnect(initial_backoff=1, max_backoff=100)

    assert connect_count["n"] == 4
    assert rebuild_calls["n"] == 3
    assert sleep_calls == [1, 2, 4]


def test_reconnect_loop_continues_if_rebuild_raises(stream, monkeypatch):
    monkeypatch.setattr(
        stream._module.time,
        "sleep",
        lambda s: None,
    )

    connect_count = {"n": 0}
    stream._ws = MagicMock()

    def fake_ws_connect():
        connect_count["n"] += 1
        if connect_count["n"] >= 2:
            stream._closing = True

    stream._ws.connect.side_effect = fake_ws_connect

    rebuild_calls = {"n": 0}

    def failing_connect():
        rebuild_calls["n"] += 1
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(stream, "connect", failing_connect)

    # Should not raise out of run_forever_with_reconnect even though
    # every rebuild attempt fails — an algo running unattended can't
    # have this loop die because one reconnect attempt failed.
    stream.run_forever_with_reconnect(initial_backoff=1, max_backoff=10)

    assert rebuild_calls["n"] == 1
    assert connect_count["n"] == 2


def test_close_sets_closing_flag_and_closes_underlying_ws(stream):
    stream._ws = MagicMock()
    stream.close()
    assert stream._closing is True
    stream._ws.close_connection.assert_called_once()
