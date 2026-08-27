"""Acceptance tests for the NSE/BSE Public API as a runtime market-data
source (the seventh entry in the Dashboard's DATA SOURCE dropdown).

Coverage contract — the acceptance criteria this file pins down:
  1. NSE/BSE is selectable alongside the six broker providers.
  2. NSE/BSE is NEVER an execution broker (capability flag + config.py
     startup rejection).
  3. Symbol -> exchange routing (NSE symbols to NSE API, BSE to BSE API).
  4. Expiry normalization at the provider boundary (DDMMMYYYY <-> dash).
  5. Runtime data-source switching WITHOUT a server restart.
  6. Switching away clears the baseline so the next tick is a FULL
     snapshot from the new source.
  7. No cross-provider contamination: a feed left running after a switch
     is gated off at broadcast time.
  8. Switching away from a broker feed stops its broadcasts (no-op sync).
  9. provider_status() reports POLLING for NSE/BSE (expected, not an
     error) and exposes the full picker shape.
 10. Startup default prefers the configured provider if credentialed, else
     the first credentialed BROKER; NSE/BSE is only the default when NO
     broker has usable credentials (login-free public API last resort).
 11. set_active_provider() rejects unknown provider keys.
 12. The option-chain pipeline routes through the ACTIVE provider at call
     time (KITE/BREEZE chain path + KITE quote overlay).
 13. Index-quote fetch is provider-aware (NSE_BSE -> {}, KITE -> spot
     quotes with market_api-shaped output).
 14. The per-tick pipeline gate activates the public NSE/BSE chain path
     (use_smartapi=False) whenever NSE_BSE is the runtime source.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from brokers import market_data as md
from brokers import market_data_registry as md_registry
from server import runtime_state, feed_manager
from application import selection_state

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "src"


@pytest.fixture(autouse=True)
def _restore_runtime_state():
    """Restore runtime-switchable state (brokers.market_data's active
    provider and the server's application selection) after every test."""
    prev_md = md.get_active_provider()
    ws = sys.modules.get("ws_server_live")
    prev_ds = (
        runtime_state.MARKET_SELECTION.data_source if ws is not None else None
    )
    yield
    try:
        md.set_active_provider(prev_md)
    except Exception:
        pass
    if ws is not None:
        runtime_state.MARKET_SELECTION.select_data_source(prev_ds)


def _noop_restart(*_a, **_k):
    return None


# ── 1. Seven selectable providers ────────────────────────────────────────
def test_seven_providers_selectable_including_nse_bse():
    keys = set(md.PROVIDER_KEYS)
    assert keys == {"SMARTAPI", "UPSTOX", "KITE", "SHOONYA", "BREEZE", "KOTAK", "NSE_BSE"}
    assert md.PROVIDER_CAPABILITIES["NSE_BSE"] == {
        "snapshot": True,
        "websocket": False,
        "execution": False,
    }
    assert md.PROVIDER_CAPABILITIES["KOTAK"] == {
        "snapshot": True,
        "websocket": True,
        "execution": False,
    }
    assert "NSE/BSE" in md.PROVIDER_DISPLAY_NAMES["NSE_BSE"]
    assert "KOTAK" in md.PROVIDER_DISPLAY_NAMES["KOTAK"]
    assert "ZERODHA" in md.PROVIDER_DISPLAY_NAMES["KITE"]


# ── 2. NSE/BSE is NOT an execution broker ────────────────────────────────
def test_nse_bse_has_no_execution_capability():
    assert md.PROVIDER_CAPABILITIES["NSE_BSE"]["execution"] is False


def test_config_rejects_nse_bse_as_execution_broker(tmp_path):
    """Canonical config must fail fast for EXECUTION_BROKER=NSE_BSE.

    Runs in an isolated temp copy of infrastructure config/paths so the repo's own
    .env (which overrides inherited env via load_dotenv(override=True))
    can't mask the guard — without the .env present, the injected
    EXECUTION_BROKER=NSE_BSE actually reaches the Settings dataclass.
    """
    import shutil

    backend = tmp_path / "backend"
    backend.mkdir()
    infrastructure = backend / "infrastructure"
    infrastructure.mkdir()
    (infrastructure / "__init__.py").write_text("")
    shutil.copy(BACKEND / "infrastructure/config.py", infrastructure / "config.py")
    shutil.copy(BACKEND / "infrastructure/paths.py", infrastructure / "paths.py")

    env = dict(os.environ)
    env["EXECUTION_BROKER"] = "NSE_BSE"
    env.pop("EXECUTION_BROKER_NSE_BSE_GUARD", None)
    env["PYTHONPATH"] = str(backend)
    probe = "from infrastructure import config"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode != 0
    assert "ValueError" in result.stderr
    assert "market-data-only" in result.stderr


@pytest.mark.parametrize("broker", ["KOTAK", "UNKNOWN"])
def test_config_rejects_non_execution_brokers(tmp_path, broker):
    """Only brokers with a live order adapter may be selected for execution."""
    import shutil

    backend = tmp_path / "backend"
    backend.mkdir()
    infrastructure = backend / "infrastructure"
    infrastructure.mkdir()
    (infrastructure / "__init__.py").write_text("")
    shutil.copy(BACKEND / "infrastructure/config.py", infrastructure / "config.py")
    shutil.copy(BACKEND / "infrastructure/paths.py", infrastructure / "paths.py")

    env = dict(os.environ)
    env["EXECUTION_BROKER"] = broker
    env["PYTHONPATH"] = str(backend)
    probe = "from infrastructure import config"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode != 0
    assert "configured execution broker" in result.stderr


# ── 3. Symbol -> exchange routing ────────────────────────────────────────
def test_symbol_to_exchange_routing():
    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "RELIANCE"):
        assert md.resolve_exchange_for_symbol(sym) == "NSE"
    for sym in ("SENSEX", "BANKEX", "SENSEX50"):
        assert md.resolve_exchange_for_symbol(sym) == "BSE"
    assert md.resolve_exchange_for_symbol("sensex") == "BSE"  # case-insensitive


# ── 4. Expiry normalization at the provider boundary ─────────────────────
def test_expiry_dash_normalization():
    assert md._normalize_expiry_dash("31JUL2026") == "31-Jul-2026"
    assert md._normalize_expiry_dash("31-Jul-2026") == "31-Jul-2026"
    assert md._normalize_expiry_dash("2026-07-31") == "31-Jul-2026"
    with pytest.raises(ValueError):
        md._normalize_expiry_dash("not-a-date")


def test_nse_bse_needs_no_credentials():
    # The login-free fallback: usable with an empty .env / no broker tokens.
    assert md.provider_has_credentials("NSE_BSE") is True


def test_upstox_spot_quote_is_a_module_function_not_an_adapter_method(monkeypatch):
    """Provider switching must be able to request an Upstox index spot."""
    from brokers.upstox import client as upstox_client

    requested = []

    def fake_quotes(instrument_key):
        requested.append(instrument_key)
        return {
            instrument_key: {
                "last_price": 25000.0,
                "ohlc": {"open": 24900.0, "high": 25100.0, "low": 24850.0, "close": 24950.0},
            }
        }

    monkeypatch.setattr(upstox_client, "get_quotes", fake_quotes)
    quote = upstox_client.get_spot_quote("NIFTY")

    assert requested == [upstox_client.INDEX_KEYS["NIFTY"]]
    assert quote["last_price"] == 25000.0


def test_upstox_spot_quote_never_substitutes_a_different_index(monkeypatch):
    """A failed SENSEX key must not be rendered with a NIFTY quote."""
    from brokers.upstox import client as upstox_client

    monkeypatch.setattr(
        upstox_client,
        "get_quotes",
        lambda _key: {upstox_client.INDEX_KEYS["NIFTY"]: {"last_price": 24229.55}},
    )

    assert upstox_client.get_spot_quote("SENSEX") is None


def test_connection_boundary_normalizes_broker_healthcheck(monkeypatch):
    from brokers import connection

    monkeypatch.setitem(connection._CHECKS, "TEST", lambda: (False, "service unavailable"))
    status = connection.check_connection("test")

    assert status.provider == "TEST"
    assert status.ready is False
    assert status.error == "service unavailable"


def test_execution_adapter_registry_loads_breeze_without_sdk_session():
    from brokers.connection import get_execution_adapter

    adapter = get_execution_adapter("BREEZE")

    assert adapter.__name__ == "brokers.breeze.client"
    assert callable(adapter.place_order)


# ── 3b. Tolerant symbol-name matching (pick a symbol by full company name) ─
def test_canonicalize_underlying_maps_company_names(ws_server_live):
    # "ZYDUS LIFESCIENCES LTD" (full company name, typed in the picker's
    # "Other…" prompt) must resolve to the ScripMaster ticker "ZYDUSLIFE" so
    # expiry/chain/token lookups actually find rows (acceptance: symbol
    # switching works for names, not just tickers).
    from brokers.smartapi.client import _canonical_underlying, list_expiries

    assert _canonical_underlying("ZYDUS LIFESCIENCES LTD") == "ZYDUSLIFE"
    assert _canonical_underlying("ZYDUSLIFE") == "ZYDUSLIFE"
    assert _canonical_underlying("RELIANCE INDUSTRIES LTD") == "RELIANCE"
    assert _canonical_underlying("TATA STEEL LTD") == "TATASTEEL"
    assert _canonical_underlying("MARUTI SUZUKI INDIA LTD") == "MARUTI"
    assert _canonical_underlying("BHARAT FORGE LTD") == "BHARATFORG"
    # Full company name -> ticker for a scrip whose ticker isn't a prefix of
    # the condensed name (ADANI ENERGY SOLUTION LTD -> ADANIENSOL). Angel's
    # master doesn't carry the full name, so this resolves via Upstox's
    # instrument dump used as a reference (acceptance: the recurring
    # "SmartAPI chain fetch empty for ADANI ENERGY SOLUTION LTD" crash
    # is gone).
    assert _canonical_underlying("ADANI ENERGY SOLUTION LTD") == "ADANIENSOL"
    assert _canonical_underlying("NIPPON L I A M LTD") == "INDNIPPON"
    assert _canonical_underlying("NIFTY") == "NIFTY"
    assert "25AUG2026" in list_expiries("ZYDUS LIFESCIENCES LTD")
    # A deliberately ambiguous name refuses rather than guessing.
    assert _canonical_underlying("TATA") is None

    # Truncated/garbled full names (a copy-pasted master name that lost its
    # middle tokens, e.g. "NIPPON L I A M LTD") can't be rescued by the
    # prefix/condense heuristic, so they land in the curated alias table
    # (acceptance: the recurring "NIPPON L I A M LTD chain fetch empty"
    # crash is resolved, not papered over by a snapshot refresh).
    assert _canonical_underlying("NIPPON L I A M LTD") == "INDNIPPON"
    assert _canonical_underlying("NIPPON LIFE INDIA AMERICAN REINSURANCE LTD") == "INDNIPPON"


def test_canonicalize_underlying_alias_table_is_resolved_directly():
    # Drives symbol_names.canonicalize_underlying against its own curated
    # alias table — no broker master needed — so the truncated-name fallback
    # has a stable, import-light test that doesn't depend on today's cache.
    from brokers.symbol_names import canonicalize_underlying, _COMMON_UNDERLYING_ALIASES

    for full in (
        "INFOSYS LIMITED", "INFOSYS LTD",
        "TATA CONSULTANCY SERVICES", "TATA CONSULTANCY SERV LTD",
        "ICICI BANK LIMITED", "ICICI BANK LTD",
        "HDFC BANK LTD", "HDFC BANK LIMITED",
        "HINDUSTAN UNILEVER LTD", "HUL LTD",
        "WIPRO LTD", "WIPRO LIMITED",
        "NIPPON LIFE INDIA AMERICAN REINSURANCE LTD",
        "NIPPON L I A M LTD", "NIPPON LIFE INDIA AMERICAN REINSURANCE",
        "NIPPON L I A M",
    ):
        assert canonicalize_underlying(full, _COMMON_UNDERLYING_ALIASES) == \
            _COMMON_UNDERLYING_ALIASES[full], full


def test_resolves_full_company_name_via_upstox_master_reference():
    # The Angel ScripMaster stores the exchange ticker in the `name` field
    # and never carries full company names, so free-text inputs like
    # "ADANI ENERGY SOLUTION LTD" can't be reverse-engineered from Angel alone.
    # _resolve_company_name_to_ticker consults Upstox's unauthenticated
    # instrument dump (EQ rows: name + clean trading_symbol) as a reference.
    from brokers.upstox.client import (
        _resolve_company_name_to_ticker,
        _COMPANY_NAME_TO_TICKER_CACHE,
    )

    assert _resolve_company_name_to_ticker("ADANI ENERGY SOLUTION LTD") == "ADANIENSOL"
    # Trailing-punctuation variant ("MARUTI SUZUKI INDIA LTD.") is treated
    # identically to the bare form by _condense's suffix stripping.
    assert _resolve_company_name_to_ticker("MARUTI SUZUKI INDIA LTD.") == "MARUTI"
    # Short/ambiguous tickers are refused rather than misrouted.
    assert _resolve_company_name_to_ticker("LT") is None
    assert _resolve_company_name_to_ticker("") is None
    assert _COMPANY_NAME_TO_TICKER_CACHE is not None


def test_upstox_find_equity_token_resolves_full_company_name(ws_server_live):
    # Regression: the Upstox spot lookup (get_spot_quote -> find_equity_token)
    # used to be called with the full company name and bail out "No token"
    # because Angel-ticker-first resolution couldn't map the full name on the
    # Upstox EQ rows. find_equity_token now consults the company-name index as
    # a fallback so the Upstox get_atm_chain path actually returns a chain
    # instead of raising "no Upstox option chain / chain fetch empty".
    from brokers.upstox.client import find_equity_token

    hit = find_equity_token("ADANI ENERGY SOLUTION LTD")
    assert hit is not None
    assert hit["trading_symbol"].upper() == "ADANIENSOL"
    assert hit["instrument_key"].startswith("NSE_EQ|")
    # The short ticker still resolves directly.
    assert find_equity_token("ADANIENSOL") is not None
    # Tata Motors equity is listed (its F&O may not be, but the EQ row resolves).
    tm = find_equity_token("TATA MOTORS LIMITED")
    assert tm is not None and tm["trading_symbol"].upper() in ("TATAMOTORS", "TMCV")


def test_upstox_canonical_name_maps_ticker_to_full_name(ws_server_live):
    # Upstox stores full company names ("ZYDUS LIFESCIENCES LTD") while the
    # dropdown holds Angel tickers ("ZYDUSLIFE"); both directions must
    # resolve to Upstox's stored name (acceptance: stock underlyings work on
    # the Upstox data source regardless of which naming the picker holds).
    from brokers.upstox.client import _canonical_name

    rows = [
        {"instrument_type": "CE", "name": "ZYDUS LIFESCIENCES LTD",
         "trading_symbol": "ZYDUSLIFE 960 CE 27 OCT 26"},
        {"instrument_type": "PE", "name": "ZYDUS LIFESCIENCES LTD",
         "trading_symbol": "ZYDUSLIFE 980 PE 27 OCT 26"},
        {"instrument_type": "EQ", "name": "ZYDUS LIFESCIENCES LTD",
         "trading_symbol": "ZYDUSLIFE"},
        {"instrument_type": "CE", "name": "INFOSYS LIMITED",
         "trading_symbol": "INFY 1850 CE 26 AUG 26"},
        {"instrument_type": "CE", "name": "HDFC BANK LTD",
         "trading_symbol": "HDFCBANK 1600 CE 27 OCT 26"},
    ]
    assert _canonical_name("ZYDUSLIFE", rows) == "ZYDUS LIFESCIENCES LTD"
    assert _canonical_name("ZYDUS LIFESCIENCES LTD", rows) == "ZYDUS LIFESCIENCES LTD"
    # Same canonical reachable via two keys (full name + ticker alias) must
    # NOT be treated as ambiguous.
    assert _canonical_name("HDFC BANK", rows) == "HDFC BANK LTD"
    assert _canonical_name("HDFCBANK", rows) == "HDFC BANK LTD"
    # Ticker alias (leading token of the option trading_symbol) maps to the
    # full name even though the ticker isn't a prefix of the condensed name.
    assert _canonical_name("INFY", rows) == "INFOSYS LIMITED"


# ── 5+6. Runtime switching without restart, full-baseline reset ──────────
def test_runtime_switch_without_restart(ws_server_live, monkeypatch):
    monkeypatch.setattr(ws_server_live, "restart_smartapi_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_upstox_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_shoonya_feed", _noop_restart)

    asyncio.run(ws_server_live.switch_data_source("NSE_BSE"))
    assert runtime_state.MARKET_SELECTION.data_source == "NSE_BSE"
    assert md.get_active_provider() == "NSE_BSE"

    # Switch to a broker provider: source + active facade both change.
    asyncio.run(ws_server_live.switch_data_source("UPSTOX"))
    assert runtime_state.MARKET_SELECTION.data_source == "UPSTOX"
    assert md.get_active_provider() == "UPSTOX"

    # And back to the public API.
    asyncio.run(ws_server_live.switch_data_source("NSE_BSE"))
    assert runtime_state.MARKET_SELECTION.data_source == "NSE_BSE"
    assert md.get_active_provider() == "NSE_BSE"


def test_switch_clears_baseline_for_full_republish(ws_server_live, monkeypatch):
    monkeypatch.setattr(ws_server_live, "restart_smartapi_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_upstox_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_shoonya_feed", _noop_restart)

    runtime_state.LAST_PAYLOAD = {"symbol": "NIFTY", "chain": []}
    runtime_state.LAST_SENT = {"symbol": "NIFTY", "chain": []}

    asyncio.run(ws_server_live.switch_data_source("KITE"))

    assert runtime_state.LAST_PAYLOAD is None
    assert runtime_state.LAST_SENT is None
    assert md.get_active_provider() == "KITE"


def test_switch_rejects_unknown_and_same_source_is_noop(ws_server_live, monkeypatch):
    monkeypatch.setattr(ws_server_live, "restart_smartapi_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_upstox_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_shoonya_feed", _noop_restart)
    with pytest.raises(ValueError):
        asyncio.run(ws_server_live.switch_data_source("NOT_A_PROVIDER"))
    before = runtime_state.MARKET_SELECTION.data_source
    result = asyncio.run(ws_server_live.switch_data_source(before))
    assert result is None


def test_switch_starts_feed_for_never_booted_provider(monkeypatch):
    """A never-started broker feed starts on the main/captured loop."""
    from types import SimpleNamespace
    from server.feed_manager import BrokerFeedManager

    main_loop = object()
    captured_loop = object()
    calls = []

    state = SimpleNamespace(
        stream=None,
        aggregator=None,
        loop=None,
        current_expiry=None,
    )

    def start(state_obj, loop, symbol, strikes_around_atm, expiry):
        calls.append((loop, symbol, strikes_around_atm, expiry))
        # Mark it running exactly as a real provider start would.
        state_obj.stream = object()
        state_obj.aggregator = object()
        state_obj.loop = loop

    manager = BrokerFeedManager(
        "SMARTAPI",
        state=state,
        start=start,
        switch=lambda *a, **k: None,
        stop=lambda *a, **k: None,
        default_symbol=lambda: "NIFTY",
        main_loop=lambda: main_loop,
        log=lambda *_: None,
    )

    # Never started: switch must start on MAIN_LOOP.
    manager.switch_blocking("NIFTY")
    assert calls == [(main_loop, "NIFTY", 10, None)]

    # Never running, but a provider-specific loop was captured:
    # that captured loop takes precedence over MAIN_LOOP.
    state.stream = None
    state.aggregator = None
    state.loop = captured_loop

    manager.switch_blocking("BANKNIFTY")
    assert calls[-1] == (captured_loop, "BANKNIFTY", 10, None)


def test_background_feed_restart_contains_start_failure():
    from types import SimpleNamespace
    from server.feed_manager import BrokerFeedManager

    logs = []
    state = SimpleNamespace(stream=None, aggregator=None, loop=object())

    def fail(*args, **kwargs):
        raise RuntimeError("invalid token")

    manager = BrokerFeedManager(
        "UPSTOX",
        state=state,
        start=fail,
        switch=fail,
        stop=lambda *args, **kwargs: None,
        default_symbol=lambda: "NIFTY",
        main_loop=lambda: state.loop,
        log=logs.append,
    )

    manager._restart_blocking("NIFTY", 10)

    assert logs == ["[upstox] Feed switch failed: invalid token"]


def test_feed_allowed_gates_stale_feeds(ws_server_live, monkeypatch):
    monkeypatch.setattr(ws_server_live, "restart_smartapi_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_upstox_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_shoonya_feed", _noop_restart)

    # Streaming provider active -> its feed is allowed.
    asyncio.run(ws_server_live.switch_data_source("UPSTOX"))
    assert feed_manager._feed_allowed("UPSTOX") is True
    assert feed_manager._feed_allowed("SMARTAPI") is False

    # Polling-only active -> NO broker feed is allowed to broadcast.
    asyncio.run(ws_server_live.switch_data_source("NSE_BSE"))
    for provider in ("SMARTAPI", "UPSTOX", "SHOONYA"):
        assert feed_manager._feed_allowed(provider) is False
    # KITE/BREEZE have no websocket client -> never "allowed" either.
    asyncio.run(ws_server_live.switch_data_source("KITE"))
    assert feed_manager._feed_allowed("KITE") is False


def test_stop_active_broker_feed_unsubscribes_without_unbound_local_error(
    ws_server_live, monkeypatch
):
    # Regression: switching away must unsubscribe both derivative and index
    # tokens through the state now owned by BrokerFeedManager.
    import time

    calls = []

    class _FakeStream:
        def unsubscribe(self, exchange, tokens):
            calls.append((exchange, list(tokens)))

    from server import feed_manager
    from server.feeds import orchestration as feed_orchestration

    state = ws_server_live.runtime_state.FEEDS["SMARTAPI"].state
    state.stream = _FakeStream()
    state.tokens = ["123", "456"]
    state.exchange = 2
    state.index_token = "26000"
    state.index_exchange = 1

    monkeypatch.setattr(
        feed_orchestration,
        "EXCHANGE_TYPE",
        {1: "NSE_CM", 2: "NFO"},
    )

    feed_manager._stop_active_broker_feed("SMARTAPI")

    deadline = time.time() + 3.0
    while time.time() < deadline and len(calls) < 2:
        time.sleep(0.05)
    assert ("NFO", ["123", "456"]) in calls
    assert ("NSE_CM", ["26000"]) in calls
    assert state.tokens is None

def test_stale_feed_broadcast_is_a_noop(ws_server_live, monkeypatch):
    asyncio.run(ws_server_live.switch_data_source("NSE_BSE"))
    asyncio.run(ws_server_live.switch_data_source("UPSTOX"))
    asyncio.run(ws_server_live.switch_data_source("NSE_BSE"))
    asyncio.run(ws_server_live.switch_data_source("KITE"))

    broadcast_calls = []
    monkeypatch.setattr(ws_server_live, "broadcast", lambda m: broadcast_calls.append(m))

    async def run():
        await ws_server_live._smartapi_sync_and_broadcast(
            {"type": "tick", "payload": {"chain": {"_keyed": True, "changed": []}}}
        )

    asyncio.run(run())
    assert broadcast_calls == [], (
        "a feed for a provider that isn't the active data source must not "
        "broadcast or merge into the payload"
    )


def test_switch_away_unsubscribes_feed_without_error(ws_server_live, monkeypatch):
    # _stop_active_broker_feed must tolerate not-yet-started feeds and still
    # run its unsubscribe best-effort (no exception).
    monkeypatch.setattr(ws_server_live, "restart_smartapi_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_upstox_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_shoonya_feed", _noop_restart)
    ws_server_live.runtime_state.FEEDS["SMARTAPI"].state.stream = None
    asyncio.run(ws_server_live.switch_data_source("SMARTAPI"))
    for provider in ("SMARTAPI", "UPSTOX", "SHOONYA"):
        ws_server_live.runtime_state.FEEDS[provider].state.stream = None


# ── 9. POLLING status is expected, not an error ──────────────────────────
def test_provider_status_reports_polling_and_shape():
    statuses = {s["id"]: s for s in md.provider_status()}
    nse_bse = statuses["NSE_BSE"]
    assert nse_bse["status"] == "POLLING"
    assert nse_bse["capabilities"]["websocket"] is False
    # Exactly one provider is active at a time, and it is the one the
    # runtime facade has been pointed at (order-independent: earlier
    # switch tests mutate the session-scoped module).
    active = [s["id"] for s in md.provider_status() if s["active"]]
    assert active == [md.get_active_provider()]
    assert md.get_active_provider() in md.PROVIDER_KEYS
    for entry in md.provider_status():
        assert {"id", "label", "status", "active", "capabilities"} <= set(entry)
        assert entry["status"] in {
            "LIVE", "AVAILABLE", "POLLING", "UNAVAILABLE", "SESSION_REQUIRED",
            "AUTH_FAILED", "SESSION_EXPIRED", "API_UNAVAILABLE", "UNKNOWN",
        }


# ── 10. Default source falls back to a credentialed broker ───────────────
def test_switch_symbol_unquotes_stale_encoded_symbol(ws_server_live, monkeypatch):
    # A stale/cached frontend bundle can still send the symbol double-encoded
    # on the wire; aiohttp decodes once, leaving "ZYDUS%20LIFESCIENCES%20LTD".
    # switch_symbol must normalize it so the engine never probes a literal
    # "%20" symbol (acceptance: symbol switches are resilient to encoding).
    monkeypatch.setattr(ws_server_live, "restart_smartapi_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_upstox_feed", _noop_restart)
    monkeypatch.setattr(ws_server_live, "restart_shoonya_feed", _noop_restart)
    monkeypatch.setattr(runtime_state, "USE_SMARTAPI", True)
    monkeypatch.setattr(runtime_state, "LIVE_FEED_PROVIDER", "SMARTAPI")

    orig_symbol = runtime_state.MARKET_SELECTION.symbol
    orig_expiry = runtime_state.MARKET_SELECTION.expiry
    try:
        ws_server_live.switch_symbol("zydus%20lifesciences%20ltd")
        assert runtime_state.MARKET_SELECTION.symbol == "ZYDUS LIFESCIENCES LTD"
        ws_server_live.switch_symbol("BANKNIFTY")  # clean input still works
        assert runtime_state.MARKET_SELECTION.symbol == "BANKNIFTY"
    finally:
        runtime_state.MARKET_SELECTION.select_symbol(orig_symbol, orig_expiry)


def test_default_data_source_falls_back_to_credentialed_broker(ws_server_live):
    expected = selection_state._resolve_default_data_source()
    assert expected != "NSE_BSE"
    assert md.provider_has_credentials(expected) is True


def test_default_data_source_nse_bse_when_no_broker_has_creds(
    ws_server_live, monkeypatch
):
    # No broker at all has usable credentials -> NSE/BSE (login-free) is
    # the last-resort default.
    monkeypatch.setattr(
        selection_state, "md_provider_has_credentials", lambda name: False
    )
    assert selection_state._resolve_default_data_source() == "NSE_BSE"


def test_breeze_requires_all_connection_credentials(monkeypatch):
    """A token alone cannot create a Breeze session or serve a price."""
    originals = {
        name: getattr(md_registry._md_settings, name)
        for name in ("breeze_api_key", "breeze_api_secret", "breeze_api_session")
    }
    try:
        object.__setattr__(md_registry._md_settings, "breeze_api_key", "KEY")
        object.__setattr__(md_registry._md_settings, "breeze_api_secret", None)
        object.__setattr__(md_registry._md_settings, "breeze_api_session", "SESSION")
        assert md.provider_has_credentials("BREEZE") is False
    finally:
        for name, value in originals.items():
            object.__setattr__(md_registry._md_settings, name, value)


# ── 11. Unknown provider keys rejected ───────────────────────────────────
def test_set_active_provider_rejects_unknown():
    with pytest.raises(ValueError):
        md.set_active_provider("BOGUS")
    # Unknown via the facade path too (ws_server_live's handler entry).
    assert md.get_active_provider() in md.PROVIDER_KEYS


# ── 12. Chain pipeline routes through the ACTIVE provider ────────────────
class _FakeChainMD:
    def __init__(self, quote_overlay=None):
        self.quote_overlay = quote_overlay or {}
        self.batch_calls = 0

    def get_atm_chain(self, underlying, expiry, strikes_around_atm=10, exchange="NFO"):
        return {
            "underlying": underlying,
            "spot": 24000.0,
            "atm_strike": 24000,
            "expiry": expiry,
            "rows": [
                {
                    "strike": 24000, "type": "CE",
                    "tradingsymbol": "NIFTY26JUL24000CE", "token": "1",
                    "lot_size": 75,
                    "ltp": 120.0, "oi": 1000.0, "volume": 500,
                    "net_change": 4.5, "pct_change": 3.9,
                },
                {
                    "strike": 24000, "type": "PE",
                    "tradingsymbol": "NIFTY26JUL24000PE", "token": "2",
                    "lot_size": 75,
                    "ltp": 80.0, "oi": 2000.0, "volume": 300,
                    "net_change": -2.25, "pct_change": -2.74,
                },
            ],
        }

    def get_batch_quotes(self, exchange, pairs, mode="FULL"):
        self.batch_calls += 1
        return {
            sym: {
                "last_price": 121.0, "oi": 1001.0, "volume": 501,
                "net_change": 5.0, "percent_change": 4.0,
            }
            for sym, _ in pairs
        }


def test_chain_pipeline_routes_by_active_provider(monkeypatch):
    from application.market_pipeline import option_chain as spa

    fake = _FakeChainMD()
    monkeypatch.setattr(spa, "market_data", fake)
    md.set_active_provider("BREEZE")

    df = spa.fetch_option_chain_wide("NIFTY", "31-Jul-2026", strikes_around_atm=2)
    assert isinstance(df, pd.DataFrame) and not df.empty
    assert {"StrikePrice", "CE_LTP", "PE_LTP", "CE_OI", "PE_OI"} <= set(df.columns)
    assert fake.batch_calls == 0  # Breeze chain rows already carry ltp/oi

    # Kite adds a live-quote overlay pass over instrument-metadata rows.
    md.set_active_provider("KITE")
    df_kite = spa.fetch_option_chain_wide("NIFTY", "31-Jul-2026", strikes_around_atm=2)
    assert isinstance(df_kite, pd.DataFrame) and not df_kite.empty
    assert fake.batch_calls == 1
    row = df_kite.iloc[0]
    # Kite OI is reported in quantity (shares) like SmartAPI's opnInterest —
    # the shared path normalizes to lots via _lot_size("NIFTY") == 75.
    assert float(row["CE_LTP"]) == 121.0
    assert float(row["CE_OI"]) == pytest.approx(1001.0 / 75)
    assert float(row["CE_Change"]) == 5.0
    assert float(row["CE_pChange"]) == 4.0


def test_chain_pipeline_canonicalizes_full_name_underlying(monkeypatch):
    # Full-company-name inputs must be canonicalized to the exchange ticker at
    # the entry point so the DataFrame Symbol column, the _day_open_oi anchor
    # keys (_chg_oi/_seed_day_anchor_from_nse) and downstream LOT_SIZES /
    # velocity lookups all agree — otherwise NSE's ticker-only seed fetch fails
    # and ChgOI degrades to an abrupt first-tick delta. The fake chain echoes
    # `underlying` back, so a non-canonical Symbol would prove the raw name
    # leaked through.
    from application.market_pipeline import option_chain as spa

    md.set_active_provider("BREEZE")
    monkeypatch.setattr(spa, "market_data", _FakeChainMD())
    monkeypatch.setattr(spa, "_seed_day_anchor_from_nse", lambda *a, **k: None)

    df = spa.fetch_option_chain_wide(
        "ADANI ENERGY SOLUTION LTD", "31-Jul-2026", strikes_around_atm=1
    )
    assert not df.empty
    assert (df["Symbol"] == "ADANIENSOL").all()


def test_canon_symbol_resolves_full_name_for_lot_and_engine_keys(monkeypatch):
    # _canon_symbol() is the option_chain_json entry-point gate shared by
    # _fetch_and_parse and _build_expiry_bundle — it must map the raw user
    # symbol to the ticker so LOT_SIZES.get(symbol), build_engine_result(symbol)
    # and the chain DataFrame's Symbol column all agree (OI lot-size scaling
    # and OI-velocity filtering silently diverge otherwise).
    from application import option_chain_runtime as ocj
    from application.pipeline_config import RuntimeConfig

    config = RuntimeConfig(symbol="NIFTY", use_smartapi=True)

    class Adapters:
        @staticmethod
        def canonicalize_symbol(symbol):
            return {"ADANI ENERGY SOLUTION LTD": "ADANIENSOL"}.get(symbol, symbol)

    assert ocj._canon_symbol("ADANI ENERGY SOLUTION LTD", config, Adapters()) == "ADANIENSOL"
    assert ocj._canon_symbol("NIFTY", config, Adapters()) == "NIFTY"
    assert ocj._canon_symbol("ADANIENSOL", config, Adapters()) == "ADANIENSOL"


def test_upstox_oi_normalized_from_shares_to_lots(monkeypatch):
    # Upstox reports OI in quantity (shares) like SmartAPI's opnInterest, but
    # _chg_oi()'s NSE anchor and the DataFrame's CE_OI/CE_ChgOI are in lots.
    # A raw share count must be divided by lot_size or OI reads lot_size× too
    # high and ChgOI (raw shares minus NSE lot anchor) is garbage. Seed a
    # known NSE anchor of 900 lots and confirm ChgOI comes out correct.
    from application.market_pipeline import option_chain as spa

    class _FakeUpstoxChainMD:
        def get_atm_chain(self, underlying, expiry, strikes_around_atm=10, exchange="NFO"):
            return {
                "underlying": underlying,
                "spot": 24000.0,
                "atm_strike": 24000,
                "expiry": expiry,
                "rows": [
                    {
                        "strike": 24000, "type": "CE",
                        "instrument_key": "NSE_FO|1", "lot_size": 75,
                        "ltp": 120.0, "oi": 75000.0, "volume": 500,
                    },
                ],
            }

    md.set_active_provider("UPSTOX")
    monkeypatch.setattr(spa, "market_data", _FakeUpstoxChainMD())
    monkeypatch.setattr(spa, "_seed_day_anchor_from_nse", lambda *a, **k: None)
    today = spa.date.today()
    spa._day_open_oi[("ADANIENSOL", "31-Jul-2026", 24000, "CE")] = (today, 900.0)

    df = spa.fetch_option_chain_wide(
        "ADANI ENERGY SOLUTION LTD", "31-Jul-2026", strikes_around_atm=1
    )
    row = df.iloc[0]
    assert float(row["CE_OI"]) == pytest.approx(75000.0 / 75)  # 1000 lots
    assert float(row["CE_ChgOI"]) == pytest.approx(1000.0 - 900.0)  # 100 lots


def test_shoonya_oi_normalized_from_shares_to_lots(monkeypatch):
    # Shoonya (Noren) also reports OI in quantity with `ls` (lot size) in the
    # quote — the shared path must convert to lots the same way as Upstox.
    from application.market_pipeline import option_chain as spa

    class _FakeShoonyaChainMD:
        def get_atm_chain(self, underlying, expiry, strikes_around_atm=10, exchange="NFO"):
            return {
                "underlying": underlying,
                "spot": 24000.0,
                "atm_strike": 24000,
                "expiry": expiry,
                "rows": [
                    {
                        "strike": 24000, "type": "CE",
                        "tradingsymbol": "NIFTY26JUL24000CE", "token": "1",
                        "lot_size": 75,
                        "ltp": 120.0, "oi": 30000.0, "volume": 500,
                    },
                ],
            }

    md.set_active_provider("SHOONYA")
    monkeypatch.setattr(spa, "market_data", _FakeShoonyaChainMD())
    monkeypatch.setattr(spa, "_seed_day_anchor_from_nse", lambda *a, **k: None)
    today = spa.date.today()
    spa._day_open_oi[("NIFTY", "31-Jul-2026", 24000, "CE")] = (today, 300.0)

    df = spa.fetch_option_chain_wide("NIFTY", "31-Jul-2026", strikes_around_atm=1)
    row = df.iloc[0]
    assert float(row["CE_OI"]) == pytest.approx(30000.0 / 75)  # 400 lots
    assert float(row["CE_ChgOI"]) == pytest.approx(400.0 - 300.0)  # 100 lots


def test_breeze_oi_normalized_from_shares_to_lots(monkeypatch):
    # Breeze's option-chain `open_interest` is raw quantity (shares) too —
    # ICICI's own SDK docs show a 2435175 OI on a NIFTY 23200 CE, i.e.
    # ~32469 lots at lot_size 75. Without the conversion the dashboard OI
    # reads lot_size× too high and ChgOI (raw shares minus NSE lot anchor)
    # is garbage, exactly like Upstox was. Breeze rows carry no lot_size,
    # so resolution falls back to the instrument-master lookup.
    from application.market_pipeline import option_chain as spa

    class _FakeBreezeChainMD:
        def get_atm_chain(self, underlying, expiry, strikes_around_atm=10, exchange="NFO"):
            return {
                "underlying": underlying,
                "spot": 24000.0,
                "atm_strike": 24000,
                "expiry": expiry,
                "rows": [
                    {
                        "strike": 24000, "type": "CE",
                        "tradingsymbol": None, "token": None,
                        "lot_size": None,
                        "ltp": 120.0, "oi": 30000.0, "volume": 500,
                    },
                ],
            }

    md.set_active_provider("BREEZE")
    monkeypatch.setattr(spa, "market_data", _FakeBreezeChainMD())
    monkeypatch.setattr(spa, "_lot_size", lambda underlying: 75)
    monkeypatch.setattr(spa, "_seed_day_anchor_from_nse", lambda *a, **k: None)
    today = spa.date.today()
    spa._day_open_oi[("NIFTY", "31-Jul-2026", 24000, "CE")] = (today, 300.0)

    df = spa.fetch_option_chain_wide("NIFTY", "31-Jul-2026", strikes_around_atm=1)
    row = df.iloc[0]
    assert float(row["CE_OI"]) == pytest.approx(30000.0 / 75)  # 400 lots
    assert float(row["CE_ChgOI"]) == pytest.approx(400.0 - 300.0)  # 100 lots



def test_broker_auth_failure_falls_back_to_public_nse_bse(monkeypatch):
    """Broker chain failure falls back to public NSE/BSE without re-scaling OI."""
    from application.market_pipeline import option_chain as spa
    from brokers import market_data_registry as md_registry
    from market.providers import nse_bse as nse_bse_module

    class _FailingBrokerMD:
        def get_atm_chain(
            self,
            underlying,
            expiry,
            strikes_around_atm=10,
            exchange="NFO",
        ):
            raise RuntimeError(
                "Shoonya login failed: HTTP 502 from QuickAuth"
            )

    class _FakeNseBse:
        def get_atm_chain(
            self,
            underlying,
            expiry,
            strikes_around_atm=10,
            exchange="NFO",
        ):
            return {
                "underlying": underlying,
                "spot": 24000.0,
                "atm_strike": 24000,
                "expiry": expiry,
                "rows": [
                    {
                        "strike": 24000,
                        "type": "CE",
                        "tradingsymbol": None,
                        "token": None,
                        "lot_size": None,
                        "ltp": 120.0,
                        "oi": 1000.0,
                        "volume": 500,
                    },
                ],
            }

    # Exercise the option-chain fallback path directly.
    # set_active_provider("SHOONYA") is intentionally NOT used because
    # the new registry correctly rejects an unhealthy broker before switching.
    monkeypatch.setattr(
        md_registry,
        "get_active_provider",
        lambda: "SHOONYA",
    )
    monkeypatch.setattr(
        spa,
        "market_data",
        _FailingBrokerMD(),
    )
    monkeypatch.setattr(
        nse_bse_module,
        "NseBseMarketData",
        _FakeNseBse,
    )
    monkeypatch.setattr(
        spa,
        "_seed_day_anchor_from_nse",
        lambda *a, **k: None,
    )

    spa._day_open_oi.clear()

    df = spa.fetch_option_chain_wide(
        "NIFTY",
        "31-Jul-2026",
        strikes_around_atm=1,
    )

    assert isinstance(df, pd.DataFrame)
    assert not df.empty

    # Public NSE/BSE OI is already in lots and must not be divided again.
    assert float(df.iloc[0]["CE_OI"]) == pytest.approx(1000.0)



class _FakeSpotMD:
    def get_spot_quote(self, symbol):
        prices = {
            "NIFTY": 24000.0,
            "BANKNIFTY": 52000.0,
            "MIDCPNIFTY": 12000.0,
            "INDIA VIX": 14.0,
            "SENSEX": 79000.0,
        }
        ltp = prices[symbol]
        return {
            "ltp": ltp,
            "close": ltp - 200.0,
        }


def test_index_quotes_provider_aware(ws_server_live, monkeypatch):
    monkeypatch.setattr(ws_server_live, "market_data", _FakeSpotMD())

    runtime_state.MARKET_SELECTION.select_data_source("NSE_BSE")
    assert ws_server_live.fetch_index_quotes_smartapi_sync() == {}

    runtime_state.MARKET_SELECTION.select_data_source("KITE")
    quotes = ws_server_live.fetch_index_quotes_smartapi_sync()
    # market_api-shaped output index_quote_loop() consumes interchangeably.
    assert set(quotes) == {"NIFTY", "BANKNIFTY", "MIDCPNIFTY", "INDIA VIX", "SENSEX"}
    assert quotes["NIFTY"]["Last Price"] == 24000.0
    assert quotes["NIFTY"]["% Change"] == pytest.approx(0.84, abs=0.01)


# ── 14. Per-tick pipeline gate activates the public chain path ───────────
def test_pipeline_gate_activates_public_nse_path(ws_server_live):
    runtime_state.MARKET_SELECTION.select_data_source("NSE_BSE")
    config = ws_server_live._build_pipeline_runtime_config("NIFTY")
    assert config.use_smartapi is False  # public NSE/BSE chain path

    runtime_state.MARKET_SELECTION.select_data_source("UPSTOX")
    config = ws_server_live._build_pipeline_runtime_config("NIFTY")
    assert config.use_smartapi is True  # broker REST chain path
