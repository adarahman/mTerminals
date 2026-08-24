from application.pipeline_config import RuntimeConfig
from application import option_chain_runtime


REMOVED_RUNTIME_GLOBALS = {
    "SYMBOL",
    "EXPIRY",
    "PRICE_SOURCE",
    "FUTURES_EXPIRY",
    "NO_EXTRA_CHAINS",
    "STRICT_EXPIRY",
    "NO_VIRTUAL_OI",
    "STRIKES_EACH_SIDE",
    "USE_SMARTAPI",
    "EXCHANGE",
    "LOOP_INTERVAL",
}


def test_option_chain_runtime_has_no_mutable_selection_globals():
    assert not (REMOVED_RUNTIME_GLOBALS & set(vars(option_chain_runtime)))
    assert not hasattr(option_chain_runtime, "set_runtime_config")


def test_cli_parser_returns_runtime_config_without_module_mutation():
    config, interval = option_chain_runtime._apply_cli_overrides(
        [
            "--symbol",
            "BANKNIFTY",
            "--expiry",
            "25-Aug-2026",
            "--interval",
            "3",
            "--strict-expiry",
        ]
    )

    assert isinstance(config, RuntimeConfig)
    assert config.symbol == "BANKNIFTY"
    assert config.expiry == "25-Aug-2026"
    assert config.strict_expiry is True
    assert interval == 3
