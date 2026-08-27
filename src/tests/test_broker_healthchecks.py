"""Tests for healthcheck() on SMARTAPI/KOTAK/KITE/UPSTOX and its wiring into
brokers.connection._CHECKS.

Before this, check_connection() only preflighted SHOONYA/BREEZE — the other
four brokers had session objects (SmartApiSession/KotakSession/KiteSession/
UpstoxSession) but no healthcheck() exposed, so check_connection() reported
them ready=True unconditionally even with an expired/missing token. These
tests exercise the new healthcheck() on each module directly, then confirm
connection.check_connection() actually calls through to it.
"""
import importlib

import pytest


# ── smartapi_client ────────────────────────────────────────────────────────

def test_smartapi_healthcheck_reports_missing_credentials(smartapi_modules, monkeypatch):
    smartapi_client, _ = smartapi_modules
    fresh = smartapi_client.SmartApiSession()
    monkeypatch.setattr(smartapi_client, "_session", fresh)
    monkeypatch.setattr(smartapi_client, "API_KEY", None)
    monkeypatch.setattr(smartapi_client, "CLIENT_CODE", None)
    monkeypatch.setattr(smartapi_client, "PIN", None)
    monkeypatch.setattr(smartapi_client, "TOTP_SECRET", None)

    ready, error = smartapi_client.healthcheck()

    assert ready is False
    assert "SMARTAPI_KEY" in error


def test_smartapi_healthcheck_reports_ready_session(smartapi_modules, monkeypatch):
    smartapi_client, _ = smartapi_modules
    fresh = smartapi_client.SmartApiSession()
    monkeypatch.setattr(fresh, "_login", lambda: None)
    monkeypatch.setattr(fresh, "_smart_api", object())
    monkeypatch.setattr(fresh, "_login_time", smartapi_client.datetime.now())
    monkeypatch.setattr(smartapi_client, "_session", fresh)

    ready, error = smartapi_client.healthcheck()

    assert ready is True
    assert error is None


# ── kotak_client ────────────────────────────────────────────────────────────

@pytest.fixture
def kotak(monkeypatch):
    module = importlib.import_module("brokers.kotak.client")
    values = {
        "kotak_consumer_key": "KEY",
        "kotak_mobile": "9999999999",
        "kotak_ucc": "UCC",
        "kotak_totp_secret": "JBSWY3DPEHPK3PXP",
        "kotak_mpin": "1234",
    }
    originals = {name: getattr(module.settings, name) for name in values}
    for name, value in values.items():
        object.__setattr__(module.settings, name, value)
    monkeypatch.setattr(module, "_session", module.KotakSession())
    yield module
    for name, value in originals.items():
        object.__setattr__(module.settings, name, value)


def test_kotak_healthcheck_reports_missing_credentials(kotak):
    object.__setattr__(kotak.settings, "kotak_mpin", None)

    ready, error = kotak.healthcheck()

    assert ready is False
    assert "KOTAK_MPIN" in error


def test_kotak_healthcheck_reports_login_failure(kotak, monkeypatch):
    def fail_factory():
        raise kotak.BrokerError("Kotak SDK unavailable")

    monkeypatch.setattr(kotak, "_session", kotak.KotakSession(api_factory=fail_factory))

    ready, error = kotak.healthcheck()

    assert ready is False
    assert "unavailable" in error


def test_kotak_healthcheck_reports_ready_session(kotak, monkeypatch):
    class FakeClient:
        def totp_login(self, **kwargs):
            return {"data": {"status": "success"}}

        def totp_validate(self, **kwargs):
            return {"data": {}}

    monkeypatch.setattr(kotak, "_session", kotak.KotakSession(api_factory=FakeClient))

    ready, error = kotak.healthcheck()

    assert ready is True
    assert error is None


# ── kite_client ─────────────────────────────────────────────────────────────

@pytest.fixture
def kite(monkeypatch):
    module = importlib.import_module("brokers.kite.client")
    monkeypatch.setattr(module, "_session", module.KiteSession())
    return module


def test_kite_healthcheck_reports_missing_token(kite, monkeypatch):
    monkeypatch.setattr(kite, "API_KEY", "KEY")
    monkeypatch.setattr(kite, "ACCESS_TOKEN", None)

    ready, error = kite.healthcheck()

    assert ready is False
    assert "KITE_ACCESS_TOKEN" in error


def test_kite_healthcheck_reports_ready_with_token(kite, monkeypatch):
    monkeypatch.setattr(kite, "API_KEY", "KEY")
    monkeypatch.setattr(kite, "ACCESS_TOKEN", "TOKEN")
    fake = type(
        "FakeKite",
        (),
        {"quote": lambda self, keys: {keys[0]: {"last_price": 25000.0}}},
    )()
    monkeypatch.setattr(kite._session, "ensure_session", lambda: fake)
    monkeypatch.setattr(
        kite, "_kite_call_with_retry", lambda _name, fn, *args: fn(*args)
    )

    ready, error = kite.healthcheck()

    assert ready is True
    assert error is None


def test_kite_healthcheck_rejects_token_without_quote_permission(kite, monkeypatch):
    monkeypatch.setattr(kite, "API_KEY", "KEY")
    monkeypatch.setattr(kite, "ACCESS_TOKEN", "TOKEN")

    def denied(_keys):
        raise kite.KiteError("Insufficient permission for that call")

    fake = type("FakeKite", (), {"quote": lambda self, keys: denied(keys)})()
    monkeypatch.setattr(kite._session, "ensure_session", lambda: fake)
    monkeypatch.setattr(
        kite, "_kite_call_with_retry", lambda _name, fn, *args: fn(*args)
    )

    ready, error = kite.healthcheck()

    assert ready is False
    assert "Insufficient permission" in error


# ── upstox_client ───────────────────────────────────────────────────────────

@pytest.fixture
def upstox(monkeypatch):
    module = importlib.import_module("brokers.upstox.client")
    monkeypatch.setattr(module, "_session", module.UpstoxSession(access_token=None))
    return module


def test_upstox_healthcheck_reports_missing_token(upstox):
    ready, error = upstox.healthcheck()

    assert ready is False
    assert "UPSTOX_ACCESS_TOKEN" in error


def test_upstox_healthcheck_reports_ready_with_token(upstox, monkeypatch):
    upstox._session.set_token("TOKEN")
    monkeypatch.setattr(
        upstox._session,
        "request",
        lambda *args, **kwargs: {"status": "success"},
    )

    ready, error = upstox.healthcheck()

    assert ready is True
    assert error is None


def test_upstox_healthcheck_rejects_an_expired_token(upstox, monkeypatch):
    upstox._session.set_token("EXPIRED")

    def reject(*args, **kwargs):
        raise upstox.UpstoxError("401: Invalid token used to access API")

    monkeypatch.setattr(upstox._session, "request", reject)

    ready, error = upstox.healthcheck()

    assert ready is False
    assert "Invalid token" in error


# ── brokers.connection wiring ────────────────────────────────────────────────

@pytest.mark.parametrize("provider", ["SMARTAPI", "KOTAK", "KITE", "UPSTOX", "SHOONYA", "BREEZE"])
def test_check_connection_covers_every_account_broker(provider):
    """Every broker with an account session must be checked against real
    state, not defaulted to ready=True. Regression guard for the previous
    gap where only SHOONYA/BREEZE were registered in _CHECKS."""
    from brokers import connection

    assert provider in connection._CHECKS


def test_check_connection_reports_unready_when_credentials_missing(monkeypatch):
    from brokers import connection

    monkeypatch.setitem(connection._CHECKS, "KITE", lambda: (False, "Missing KITE_ACCESS_TOKEN"))

    status = connection.check_connection("KITE")

    assert status.ready is False
    assert status.error == "Missing KITE_ACCESS_TOKEN"


@pytest.mark.parametrize(
    "error",
    [
        "Insufficient permission for that call",
        "401 Unauthorized",
        "HTTP 403",
    ],
)
def test_check_connection_classifies_permission_failures_as_auth_failed(
    monkeypatch, error
):
    from brokers import connection

    monkeypatch.setitem(connection._CHECKS, "KITE", lambda: (False, error))

    status = connection.check_connection("KITE")

    assert status.ready is False
    assert status.status is connection.BrokerStatus.AUTH_FAILED


def test_check_connection_reports_ready_when_session_usable(monkeypatch):
    from brokers import connection

    monkeypatch.setitem(connection._CHECKS, "UPSTOX", lambda: (True, None))

    status = connection.check_connection("UPSTOX")

    assert status.ready is True
    assert status.error is None


def test_data_only_provider_stays_ready_without_a_check():
    """NSE_BSE has no account session, so it must still default to ready
    rather than being pulled into the new per-broker checks."""
    from brokers import connection

    status = connection.check_connection("NSE_BSE")

    assert status.ready is True
