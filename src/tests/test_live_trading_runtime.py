from pathlib import Path

from server.live_trading_runtime import LiveOrderTokenResolver, LiveTradingConfig


def test_live_trading_config_reads_environment_policy():
    config = LiveTradingConfig.from_environment(
        Path("/srv/mterminals"),
        {
            "LIVE_TRADING_ENABLED": "TRUE",
            "LIVE_MAX_LOTS_PER_ORDER": "3",
            "LIVE_MAX_ORDERS_PER_MINUTE": "8",
            "POSITION_RECONCILE_SECONDS": "45",
        },
    )

    assert config.enabled is True
    assert config.kill_switch_file == "/srv/mterminals/LIVE_TRADING_KILL"
    assert config.max_lots_per_order == 3
    assert config.max_orders_per_minute == 8
    assert config.reconcile_seconds == 45


def test_live_trading_config_defaults_to_safe_disabled_policy():
    config = LiveTradingConfig.from_environment(Path("/srv/mterminals"), {})

    assert config.enabled is False
    assert config.max_lots_per_order == 1
    assert config.max_orders_per_minute == 5
    assert config.reconcile_seconds == 120


def test_token_resolver_prefers_execution_broker_contract_resolution():
    calls = []
    resolver = LiveOrderTokenResolver(
        bse_symbols={"SENSEX"},
        resolve_option_contract=lambda *args: calls.append(args) or ("BFO", "S", "1"),
        find_option_token=lambda *_args: None,
    )

    result = resolver.resolve("SENSEX", "CE", "28-Aug-2026", 81000)

    assert result == ("BFO", "S", "1")
    assert calls == [("SENSEX", "28-Aug-2026", 81000, "CE", "BFO")]


def test_token_resolver_normalizes_expiry_for_fallback_lookup():
    calls = []
    resolver = LiveOrderTokenResolver(
        bse_symbols=set(),
        resolve_option_contract=None,
        find_option_token=lambda *args: calls.append(args)
        or {"tradingsymbol": "NIFTY26AUG25000CE", "token": "123"},
    )

    result = resolver.resolve("NIFTY", "CE", "28-Aug-2026", 25000)

    assert result == ("NFO", "NIFTY26AUG25000CE", "123")
    assert calls == [("NIFTY", "28AUG2026", 25000, "CE", "NFO")]
    assert resolver.resolve("NIFTY", "FUT", "28-Aug-2026", 0) is None
    assert resolver.resolve("NIFTY", "CE", "invalid", 25000) is None
