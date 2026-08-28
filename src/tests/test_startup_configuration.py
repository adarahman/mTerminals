from types import SimpleNamespace

from server import startup_configuration


def _args(**overrides):
    values = {
        "symbol": " nifty ",
        "expiry": "01-Sep-2026",
        "poll_seconds": 10,
        "pipeline_timeout_seconds": 0.5,
        "min_tick_recompute_seconds": 3,
        "host": "127.0.0.1",
        "port": 8765,
        "http_port": 5500,
        "relay": False,
        "no_delta": False,
        "no_index_quotes": False,
        "index_quote_seconds": 20,
        "funds_poll_seconds": 30,
        "portfolio_poll_seconds": 0,
        "strikes_each_side": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_broker_startup_configures_selection_and_websocket_overlay(monkeypatch):
    state = SimpleNamespace()
    activated = []
    monkeypatch.setattr(
        startup_configuration.selection_state,
        "_resolve_default_data_source",
        lambda: "UPSTOX",
    )

    config = startup_configuration.configure_startup(
        args=_args(),
        runtime_state=state,
        broker_services_enabled=True,
        live_feed_provider="UPSTOX",
        activate_provider=activated.append,
        supports_websocket=lambda provider: provider == "UPSTOX",
    )

    assert activated == ["UPSTOX"]
    assert state.MARKET_SELECTION.symbol == "NIFTY"
    assert state.MARKET_SELECTION.data_source == "UPSTOX"
    assert state.STRIKES_EACH_SIDE == 15
    assert state.PIPELINE_TIMEOUT_SECONDS == 1.0
    assert "Upstox REST" in config.feed_summary
    assert "UPSTOX websocket overlay ENABLED" in config.feed_summary
    assert "every Upstox tick" in config.portfolio_summary


def test_public_startup_forces_public_source_and_wider_chain(monkeypatch):
    state = SimpleNamespace()
    activated = []
    monkeypatch.setattr(
        startup_configuration.selection_state,
        "_resolve_default_data_source",
        lambda: "KOTAK",
    )

    config = startup_configuration.configure_startup(
        args=_args(portfolio_poll_seconds=0.5),
        runtime_state=state,
        broker_services_enabled=False,
        live_feed_provider="KOTAK",
        activate_provider=activated.append,
        supports_websocket=lambda _provider: True,
    )

    assert activated == ["NSE_BSE"]
    assert state.MARKET_SELECTION.data_source == "NSE_BSE"
    assert state.USE_SMARTAPI is False
    assert state.STRIKES_EACH_SIDE == 50
    assert "NSE/BSE public REST (polling)" in config.feed_summary
    assert "inactive — public-only mode" in config.portfolio_summary
