from brokers.option_chain_adapter import BrokerOptionChainAdapter


def test_broker_option_chain_adapter_canonicalizes_and_fetches():
    calls = []
    adapter = BrokerOptionChainAdapter(
        canonicalize_symbol=lambda symbol: "ZYDUSLIFE",
        fetch_chain=lambda *args, **kwargs: calls.append((args, kwargs)) or "chain",
    )

    result = adapter.fetch(
        " zydus lifesciences ltd ",
        "27-Aug-2026",
        "NFO",
        15,
    )

    assert result == "chain"
    assert calls == [
        (
            ("ZYDUSLIFE", "27-Aug-2026"),
            {"exchange": "NFO", "strikes_around_atm": 15},
        )
    ]


def test_broker_option_chain_adapter_falls_back_to_normalized_symbol():
    def fail(symbol):
        raise RuntimeError("instrument master unavailable")

    adapter = BrokerOptionChainAdapter(
        canonicalize_symbol=fail,
        fetch_chain=lambda *args, **kwargs: None,
    )

    assert adapter.canonicalize(" nifty ") == "NIFTY"
    assert adapter.canonicalize("") == ""
