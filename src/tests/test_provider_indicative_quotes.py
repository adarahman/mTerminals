import asyncio

from application import provider_indicative_quotes
from brokers import market_data_registry
from server.background_loops import ProviderIndicativeQuoteLoop


def test_quote_cache_is_symbol_scoped_and_returns_copies():
    provider_indicative_quotes.store_provider_spot_quotes(
        "NIFTY",
        {"KITE": {"price": 25123.45, "asOf": "2026-11-09T10:00:00+05:30"}},
    )

    quotes = provider_indicative_quotes.get_provider_spot_quotes("NIFTY")
    quotes["KITE"]["price"] = 0

    assert provider_indicative_quotes.get_provider_spot_quotes("NIFTY")["KITE"]["price"] == 25123.45
    assert provider_indicative_quotes.get_provider_spot_quotes("BANKNIFTY") == {}


def test_fetch_provider_quotes_only_queries_configured_sources(monkeypatch):
    class QuoteProvider:
        def __init__(self, value):
            self.value = value

        def get_spot_quote(self, _symbol):
            return self.value

    monkeypatch.setattr(market_data_registry, "PROVIDER_KEYS", ("SMARTAPI", "UPSTOX", "NSE_BSE"))
    monkeypatch.setattr(
        market_data_registry,
        "_PROVIDERS",
        {
            "SMARTAPI": lambda: QuoteProvider({"ltp": 25123.456}),
            "UPSTOX": lambda: QuoteProvider({"ltp": 0}),
            "NSE_BSE": lambda: QuoteProvider({"last_price": 25124}),
        },
    )
    monkeypatch.setattr(
        market_data_registry,
        "provider_has_credentials",
        lambda key: key == "SMARTAPI",
    )

    quotes = market_data_registry.fetch_provider_spot_quotes("nifty")

    assert set(quotes) == {"SMARTAPI", "NSE_BSE"}
    assert quotes["SMARTAPI"]["price"] == 25123.46
    assert quotes["NSE_BSE"]["price"] == 25124
    assert quotes["SMARTAPI"]["asOf"]


def test_quote_loop_stores_and_broadcasts_provider_quotes():
    calls = []

    async def broadcast(message):
        calls.append(("broadcast", message))

    loop = ProviderIndicativeQuoteLoop(
        symbol=lambda: "NIFTY",
        fetch_quotes=lambda symbol: {
            "KITE": {"price": 25123.45, "asOf": "2026-11-09T10:00:00+05:30"}
        },
        store_quotes=lambda symbol, quotes: calls.append(("store", symbol, quotes)),
        broadcast=broadcast,
        poll_seconds=60,
    )

    asyncio.run(loop.tick())

    assert calls == [
        (
            "store",
            "NIFTY",
            {"KITE": {"price": 25123.45, "asOf": "2026-11-09T10:00:00+05:30"}},
        ),
        (
            "broadcast",
            {
                "type": "providerIndicativeQuotes",
                "payload": {
                    "symbol": "NIFTY",
                    "quotes": {
                        "KITE": {
                            "price": 25123.45,
                            "asOf": "2026-11-09T10:00:00+05:30",
                        }
                    },
                },
            },
        ),
    ]
