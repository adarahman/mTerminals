from brokers.base import (
    ExecutionBroker,
    MarketData,
    MarketDataProvider,
    missing_execution_methods,
)
from brokers.market_data import MarketData as CompatibilityMarketData


class _MarketDataStub:
    list_expiries = lambda self, *args, **kwargs: []
    get_atm_chain = lambda self, *args, **kwargs: None
    find_option_token = lambda self, *args, **kwargs: None
    get_batch_quotes = lambda self, *args, **kwargs: {}
    get_batch_quotes_by_token = lambda self, *args, **kwargs: {}
    get_spot_quote = lambda self, *args, **kwargs: None
    get_futures_quote = lambda self, *args, **kwargs: None
    get_fno_underlyings = lambda self, *args, **kwargs: {}
    index_tokens = lambda self: {}


class _ExecutionStub:
    place_order = lambda self, *args, **kwargs: "order-id"
    get_order_book = lambda self: []
    get_positions = lambda self: []
    get_funds = lambda self: {}


def test_market_data_contract_has_one_canonical_definition():
    assert MarketData is MarketDataProvider
    assert CompatibilityMarketData is MarketDataProvider
    assert isinstance(_MarketDataStub(), MarketDataProvider)


def test_execution_contract_checks_real_runtime_surface():
    adapter = _ExecutionStub()
    assert isinstance(adapter, ExecutionBroker)
    assert missing_execution_methods(adapter) == []
    assert missing_execution_methods(object()) == [
        "place_order",
        "get_order_book",
        "get_positions",
        "get_funds",
    ]
