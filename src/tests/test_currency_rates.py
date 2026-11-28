import asyncio

from brokers.smartapi.instruments import InstrumentResolver
from brokers.smartapi.market_data import SmartApiMarketData
from server.currency_rates_api import CurrencyRatesApi


def test_currency_future_resolver_selects_requested_live_expiry_slot(tmp_path):
    resolver = InstrumentResolver(cache_dir=tmp_path)
    resolver._instruments = [
        {
            "exch_seg": "CDS", "instrumenttype": "FUTCUR", "name": "USDINR",
            "expiry": expiry, "symbol": f"USDINR{expiry}", "token": str(index),
        }
        for index, expiry in enumerate(("30JUN2099", "31JUL2099", "31AUG2099"), 1)
    ]

    assert resolver.get_currency_futures_contract("USDINR", which="NEAR")["token"] == "1"
    assert resolver.get_currency_futures_contract("USDINR", which="NEXT")["token"] == "2"
    assert resolver.get_currency_futures_contract("USDINR", which="FAR")["token"] == "3"
    assert resolver.get_currency_futures_contract("USDINR", which="INVALID")["token"] == "1"


def test_currency_quote_api_passes_selected_expiry_to_provider():
    seen = []

    class Provider:
        def get_currency_futures_quote(self, symbol, which):
            seen.append((symbol, which))
            return {"LTP": 83.42}

    class Request:
        query = {"symbol": "USDINR", "which": "NEXT"}

    response = asyncio.run(CurrencyRatesApi(
        market_data=lambda: Provider(), active_provider=lambda: "SMARTAPI"
    ).quote(Request()))

    assert response.status == 200
    assert seen == [("USDINR", "NEXT")]


def test_currency_quote_adapter_resolves_selected_expiry(monkeypatch):
    seen = []
    future = {"symbol": "USDINR31JUL2099FUT", "token": "42", "expiry": "31JUL2099", "lotsize": "1000"}
    monkeypatch.setattr(
        "brokers.smartapi.instruments.get_currency_futures_contract",
        lambda symbol, which="NEAR": seen.append((symbol, which)) or future,
    )
    adapter = SmartApiMarketData()
    monkeypatch.setattr(
        adapter, "get_batch_quotes_by_token",
        lambda exchange, pairs, mode: {"42": {"ltp": 83.42, "close": 83.38}},
    )

    quote = adapter.get_currency_futures_quote("USDINR", "FAR")

    assert seen == [("USDINR", "FAR")]
    assert quote["LTP"] == 83.42
    assert quote["LotSize"] == 1000


def test_currency_quote_api_rejects_invalid_expiry_slot():
    class Provider:
        def get_currency_futures_quote(self, symbol, which):
            raise AssertionError("provider must not be called")

    class Request:
        query = {"symbol": "USDINR", "which": "INVALID"}

    response = asyncio.run(CurrencyRatesApi(
        market_data=lambda: Provider(), active_provider=lambda: "SMARTAPI"
    ).quote(Request()))

    assert response.status == 400
