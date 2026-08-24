"""Unit tests for brokers/smartapi_client.py's SmartApiSession — the
session/retry/re-login layer everything else in that module funnels
through via SmartApiSession.call(). None of this had direct test
coverage before (see conftest.py's smartapi_modules fixture for why the
module previously couldn't even be imported in a test process), even
though an unattended algo depends entirely on this layer recovering from
network blips, rate limits, and expired sessions on its own.

These tests never touch the network or a real SmartConnect instance —
SmartApiSession._login() is monkeypatched to install a MagicMock in
place of the real SmartAPI SDK object, and `time.sleep` /
`_rate_limit_wait` are stubbed out so retry/backoff paths run instantly
instead of actually sleeping.
"""
import requests
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def session(smartapi_modules, monkeypatch):
    smartapi_client, _ = smartapi_modules

    # Never actually hit the rate-limit spacing sleep in these tests —
    # the module-level _rate_limit_last_ts dict persists across tests in
    # the session, so without this, unrelated tests calling the same
    # fn_name back-to-back would pick up real (if small) sleeps.
    monkeypatch.setattr(smartapi_client, "_rate_limit_wait", lambda fn_name: None)
    # Retry/backoff paths call time.sleep directly (not _rate_limit_wait)
    # — stub that too so backoff tests don't actually wait.
    monkeypatch.setattr(smartapi_client.time, "sleep", lambda *a, **k: None)

    sess = smartapi_client.SmartApiSession()
    mock_api = MagicMock()
    login_calls = {"n": 0}

    def fake_login():
        # Simplification: every (re-)login installs the SAME mock object
        # rather than a fresh SmartConnect instance, since these tests
        # care about call()'s retry/re-login *decisions*, not about
        # SmartConnect construction itself.
        login_calls["n"] += 1
        sess._smart_api = mock_api
        sess._login_time = smartapi_client.datetime.now()

    monkeypatch.setattr(sess, "_login", fake_login)

    sess.mock_api = mock_api
    sess.login_calls = login_calls
    return sess


# ── ensure_session / login caching ──────────────────────────────────────

def test_ensure_session_logs_in_once_and_reuses_same_day(session):
    session.ensure_session()
    session.ensure_session()
    assert session.login_calls["n"] == 1


def test_ensure_session_relogs_in_after_stale_day(session):
    session.ensure_session()
    assert session.login_calls["n"] == 1

    # Simulate crossing midnight without a restart.
    session._login_time = session._login_time.replace(year=session._login_time.year - 1)
    session.ensure_session()
    assert session.login_calls["n"] == 2


# ── happy path ───────────────────────────────────────────────────────────

def test_call_happy_path_returns_result_unmodified(session):
    session.mock_api.someMethod.return_value = {"status": True, "data": {"x": 1}}
    result = session.call("someMethod", 1, 2, kw=3)
    assert result == {"status": True, "data": {"x": 1}}
    session.mock_api.someMethod.assert_called_once_with(1, 2, kw=3)
    assert session.login_calls["n"] == 1


def test_call_result_with_unrelated_error_code_returned_as_is(session):
    """An error dict whose errorcode isn't one of the recognized
    rate-limit/token codes (e.g. a plain order rejection) should be
    passed straight back to the caller, not retried."""
    rejected = {"status": False, "errorcode": "AB9999", "message": "Order rejected: price out of range"}
    session.mock_api.someMethod.return_value = rejected
    result = session.call("someMethod")
    assert result == rejected
    assert session.mock_api.someMethod.call_count == 1
    assert session.login_calls["n"] == 1


# ── transient network errors: retry once, no re-login ───────────────────

def test_call_retries_once_on_network_error_without_relogin(session):
    session.mock_api.someMethod.side_effect = [
        requests.exceptions.ConnectionError("connection reset"),
        {"status": True, "data": "ok"},
    ]
    result = session.call("someMethod")
    assert result == {"status": True, "data": "ok"}
    assert session.mock_api.someMethod.call_count == 2
    assert session.login_calls["n"] == 1  # no re-login for a plain network blip


def test_call_network_error_persists_returns_status_false(session):
    session.mock_api.someMethod.side_effect = [
        requests.exceptions.Timeout("timed out"),
        requests.exceptions.Timeout("timed out again"),
    ]
    result = session.call("someMethod")
    assert result["status"] is False
    assert "timed out again" in result["message"]
    assert session.login_calls["n"] == 1


# ── rate limiting: escalating backoff, no re-login ──────────────────────

def test_call_rate_limited_exception_retries_then_succeeds(session):
    session.mock_api.someMethod.side_effect = [
        Exception("Access denied because of exceeding access rate"),
        {"status": True, "data": "recovered"},
    ]
    result = session.call("someMethod")
    assert result == {"status": True, "data": "recovered"}
    assert session.login_calls["n"] == 1  # rate limiting is not an auth problem


def test_call_rate_limited_result_dict_retries_then_succeeds(session):
    session.mock_api.someMethod.side_effect = [
        {"status": False, "message": "Access rate exceeded"},
        {"status": True, "data": "ok"},
    ]
    result = session.call("someMethod")
    assert result == {"status": True, "data": "ok"}
    assert session.login_calls["n"] == 1


def test_call_rate_limited_exhausts_retries_returns_status_false(session):
    from brokers.smartapi.client import _RATE_LIMIT_MAX_RETRIES
    always_rate_limited = Exception("too many requests")
    # 1 initial attempt (in call()) + _RATE_LIMIT_MAX_RETRIES attempts in
    # _retry_after_rate_limit — all rate-limited, all give up eventually.
    total_attempts = 1 + _RATE_LIMIT_MAX_RETRIES
    session.mock_api.someMethod.side_effect = [always_rate_limited] * total_attempts
    result = session.call("someMethod")
    assert result["status"] is False
    assert session.mock_api.someMethod.call_count == total_attempts
    assert session.login_calls["n"] == 1  # still never treated as an auth problem


# ── generic/unrecognized exceptions: re-login and retry once ────────────

def test_call_generic_exception_triggers_relogin_and_retries(session):
    session.mock_api.someMethod.side_effect = [
        ValueError("some unexpected SDK error"),
        {"status": True, "data": "recovered"},
    ]
    result = session.call("someMethod")
    assert result == {"status": True, "data": "recovered"}
    assert session.login_calls["n"] == 2  # initial login + the re-login retry
    assert session.mock_api.someMethod.call_count == 2


# ── recognized token/session error codes: re-login and retry once ───────

@pytest.mark.parametrize("errorcode", ["AG8001", "AG8002", "AB1010", "AB1050"])
def test_call_token_error_code_triggers_relogin_and_retries(session, errorcode):
    session.mock_api.someMethod.side_effect = [
        {"status": False, "errorcode": errorcode, "message": "invalid/expired token"},
        {"status": True, "data": "ok"},
    ]
    result = session.call("someMethod")
    assert result == {"status": True, "data": "ok"}
    assert session.login_calls["n"] == 2
    assert session.mock_api.someMethod.call_count == 2


def test_place_order_reuses_existing_caller_tag_without_submission(smartapi_modules, monkeypatch):
    smartapi_client, _ = smartapi_modules
    monkeypatch.setattr(smartapi_client, "_find_order_by_tag", lambda tag, **kwargs: "ORDER123")
    submit_calls = []
    monkeypatch.setattr(
        smartapi_client._session, "call",
        lambda *args, **kwargs: submit_calls.append((args, kwargs)),
    )

    result = smartapi_client.place_order(
        "NIFTY", "123", "NFO", "BUY", 65, order_tag="liveorder00000001",
    )

    assert result == "ORDER123"
    assert submit_calls == []


def test_place_order_fails_closed_when_tag_preflight_is_unavailable(smartapi_modules, monkeypatch):
    smartapi_client, _ = smartapi_modules

    def unavailable(tag, **kwargs):
        raise RuntimeError("order book unavailable")

    monkeypatch.setattr(smartapi_client, "_find_order_by_tag", unavailable)
    submit_calls = []
    monkeypatch.setattr(
        smartapi_client._session, "call",
        lambda *args, **kwargs: submit_calls.append((args, kwargs)),
    )

    with pytest.raises(smartapi_client.BrokerError, match="cannot verify order tag"):
        smartapi_client.place_order(
            "NIFTY", "123", "NFO", "BUY", 65, order_tag="liveorder00000001",
        )
    assert submit_calls == []
