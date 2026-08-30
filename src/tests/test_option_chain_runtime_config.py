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


def test_option_chain_runtime_is_not_a_stranded_executable_entrypoint():
    assert not hasattr(option_chain_runtime, "_build_arg_parser")
    assert not hasattr(option_chain_runtime, "_apply_cli_overrides")
