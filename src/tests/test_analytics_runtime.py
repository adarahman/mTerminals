import asyncio

from server.analytics_runtime import AnalyticsRuntime


def _runtime(**overrides):
    state = {
        "source": "UPSTOX",
        "activated": [],
        "captured": None,
        "clears": 0,
        "configs": [],
    }

    def clear_capture():
        state["clears"] += 1
        state["captured"] = None

    def invoke(config, **kwargs):
        state["configs"].append(config)
        state["captured"] = {
            "symbol": config.symbol,
            "adapters": kwargs["broker_adapters"],
        }

    defaults = {
        "symbol": lambda: "NIFTY",
        "expiry": lambda: "01-Sep-2026",
        "data_source": lambda: state["source"],
        "price_source": lambda: "AUTO",
        "futures_expiry": lambda: "NEAR",
        "strikes_each_side": lambda: 15,
        "activate_provider": state["activated"].append,
        "resolve_default_expiry": lambda _symbol: "08-Sep-2026",
        "apply_config": lambda _config: None,
        "clear_capture": clear_capture,
        "captured_payload": lambda: state["captured"],
        "export_dashboard": lambda *_args, **_kwargs: None,
        "invoke_analytics": invoke,
        "broker_adapters": "broker-adapters",
        "extra_chains": False,
        "strict_expiry": True,
        "no_virtual_oi": True,
        "operation_timeout_seconds": 7.5,
    }
    defaults.update(overrides)
    return AnalyticsRuntime(**defaults), state


def test_configure_current_owns_complete_runtime_policy():
    runtime, state = _runtime()

    config = runtime.configure_current()

    assert state["activated"] == ["UPSTOX"]
    assert config.symbol == "NIFTY"
    assert config.expiry == "01-Sep-2026"
    assert config.no_extra_chains is True
    assert config.strict_expiry is True
    assert config.no_virtual_oi is True
    assert config.price_source == "AUTO"
    assert config.futures_expiry == "NEAR"
    assert config.strikes_each_side == 15
    assert config.use_smartapi is True
    assert config.operation_timeout_seconds == 7.5


def test_diagnostic_config_uses_current_provider_and_default_expiry():
    runtime, state = _runtime()
    state["source"] = "NSE_BSE"

    config = runtime.configure("SENSEX")

    assert state["activated"] == ["NSE_BSE"]
    assert config.expiry == "08-Sep-2026"
    assert config.use_smartapi is False


def test_run_clears_capture_invokes_pipeline_and_returns_payload():
    runtime, state = _runtime()

    result = asyncio.run(runtime.run())

    assert state["clears"] == 1
    assert len(state["configs"]) == 1
    assert result == {"symbol": "NIFTY", "adapters": "broker-adapters"}
