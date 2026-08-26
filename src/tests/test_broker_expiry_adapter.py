from brokers.expiry_adapter import BrokerExpiryAdapter


class MarketData:
    def __init__(self, values=None, error=None):
        self.values = values or []
        self.error = error

    def list_expiries(self, symbol, exchange):
        if self.error:
            raise self.error
        return self.values


def test_broker_expiry_adapter_normalizes_provider_expiries():
    adapter = BrokerExpiryAdapter(
        fallback=lambda symbol: ["fallback"],
        active_provider=lambda: "UPSTOX",
        provider_market_data=MarketData(
            ["27AUG2026", "03-Sep-2026", "invalid"]
        ),
    )

    assert adapter.list_expiries("NIFTY", "NFO") == [
        "27-Aug-2026",
        "03-Sep-2026",
    ]


def test_broker_expiry_adapter_falls_back_on_provider_failure():
    adapter = BrokerExpiryAdapter(
        fallback=lambda symbol: ["10-Sep-2026"],
        active_provider=lambda: "KITE",
        provider_market_data=MarketData(error=RuntimeError("offline")),
    )

    assert adapter.list_expiries("NIFTY", "NFO") == ["10-Sep-2026"]


def test_broker_expiry_adapter_uses_fallback_for_non_direct_provider():
    adapter = BrokerExpiryAdapter(
        fallback=lambda symbol: ["10-Sep-2026"],
        active_provider=lambda: "NSE_BSE",
        provider_market_data=MarketData(["must-not-be-used"]),
    )

    assert adapter.list_expiries("NIFTY", "NFO") == ["10-Sep-2026"]


def test_broker_expiry_adapter_caches_by_provider_symbol_and_exchange():
    market_data = MarketData(["27AUG2026"])
    calls = []
    original = market_data.list_expiries
    market_data.list_expiries = lambda symbol, exchange: (
        calls.append((symbol, exchange)) or original(symbol, exchange)
    )
    adapter = BrokerExpiryAdapter(
        fallback=lambda symbol: [],
        active_provider=lambda: "UPSTOX",
        provider_market_data=market_data,
        cache_ttl_seconds=60,
    )

    assert adapter.list_expiries("NIFTY", "NFO") == ["27-Aug-2026"]
    assert adapter.list_expiries("NIFTY", "NFO") == ["27-Aug-2026"]
    assert calls == [("NIFTY", "NFO")]
