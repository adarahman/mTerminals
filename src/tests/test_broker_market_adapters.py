from market.option_chain.runtime_adapters import BrokerMarketAdapters


def test_broker_market_adapters_are_explicit_and_immutable():
    noop = lambda *args: None
    adapters = BrokerMarketAdapters(
        canonicalize_symbol=lambda symbol: symbol.upper(),
        fetch_chain=noop,
        list_expiries=lambda symbol, exchange: [],
        fetch_futures=noop,
        warm_batch=noop,
        fetch_ticker_payload=noop,
        fetch_vix=noop,
        fetch_sensex_quote=noop,
    )

    assert adapters.canonicalize_symbol("nifty") == "NIFTY"
