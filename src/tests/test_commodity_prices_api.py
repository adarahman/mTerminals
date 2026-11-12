import asyncio
import json
from types import SimpleNamespace

from server.commodity_prices_api import CommodityPricesApi


class _Provider:
    def __init__(self, quote=None, expiries=None, chain=None):
        self.quote = quote
        self.expiries = expiries or []
        self.chain = chain
        self.requested = []

    def get_commodity_futures_quote(self, symbol):
        self.requested.append(symbol)
        return self.quote

    def get_commodity_option_expiries(self, symbol):
        self.requested.append(("expiries", symbol))
        return self.expiries

    def get_commodity_option_chain(self, symbol, expiry, strikes_around_atm=10):
        self.requested.append(("chain", symbol, expiry, strikes_around_atm))
        return self.chain


def _request(symbol="CRUDEOIL"):
    return SimpleNamespace(query={"symbol": symbol})


def test_commodity_quote_returns_provider_and_market_quote():
    quote = {"Contract": "CRUDEOILFUT", "LTP": 7123.5}
    provider = _Provider(quote)
    api = CommodityPricesApi(
        market_data=lambda: provider,
        active_provider=lambda: "SMARTAPI",
    )

    response = asyncio.run(api.quote(_request()))

    assert response.status == 200
    assert json.loads(response.text) == {
        "provider": "SMARTAPI",
        "symbol": "CRUDEOIL",
        "quote": quote,
    }
    assert provider.requested == ["CRUDEOIL"]


def test_commodity_quote_rejects_unknown_symbol_without_provider_call():
    provider = _Provider({"LTP": 1})
    api = CommodityPricesApi(market_data=lambda: provider)

    response = asyncio.run(api.quote(_request("ARBITRARY")))

    assert response.status == 400
    assert json.loads(response.text) == {"error": "Unsupported commodity symbol."}
    assert provider.requested == []


def test_commodity_quote_explains_unsupported_active_provider():
    provider = object()
    api = CommodityPricesApi(
        market_data=lambda: provider,
        active_provider=lambda: "NSE_BSE",
    )

    response = asyncio.run(api.quote(_request()))

    assert response.status == 501
    payload = json.loads(response.text)
    assert payload["provider"] == "NSE_BSE"
    assert "Select Angel One SmartAPI" in payload["error"]


def test_commodity_option_chain_defaults_to_nearest_expiry():
    chain = {
        "atm_strike": 6000,
        "future_change_pct": 0.5,
        "rows": [
            {"strike": 6000, "type": "CE", "oi": 100, "volume": 20},
            {"strike": 6000, "type": "PE", "oi": 200, "volume": 40},
        ],
    }
    provider = _Provider(expiries=["31DEC2099"], chain=chain)
    api = CommodityPricesApi(
        market_data=lambda: provider,
        active_provider=lambda: "SMARTAPI",
    )

    response = asyncio.run(api.option_chain(_request()))

    assert response.status == 200
    assert json.loads(response.text) == {
        "provider": "SMARTAPI",
        "symbol": "CRUDEOIL",
        "expiry": "31DEC2099",
        "expiries": ["31DEC2099"],
        "chain": chain,
        "analysis": {
            "verdict": "BULLISH",
            "reason": "Put OI is higher than call OI and the underlying futures are up on the day.",
            "put_call_oi_ratio": 2.0,
            "call_oi": 100.0,
            "put_oi": 200.0,
            "call_volume": 20.0,
            "put_volume": 40.0,
            "call_oi_wall": 6000,
            "put_oi_wall": 6000,
            "futures_change_pct": 0.5,
            "scope": "Visible ATM ±10 strikes; OI is current positioning, not OI change.",
            "method": (
                "Bullish: PCR > 1 and futures up; bearish: PCR < 1 and futures down; "
                "conflicting or flat: neutral; missing inputs: wait."
            ),
        },
    }
    assert provider.requested == [
        ("expiries", "CRUDEOIL"),
        ("chain", "CRUDEOIL", "31DEC2099", 10),
    ]


def test_commodity_option_chain_rejects_unavailable_expiry():
    provider = _Provider(expiries=["31DEC2099"])
    api = CommodityPricesApi(market_data=lambda: provider)
    request = SimpleNamespace(query={"symbol": "CRUDEOIL", "expiry": "01JAN2099"})

    response = asyncio.run(api.option_chain(request))

    assert response.status == 400
    assert json.loads(response.text) == {
        "error": "Expiry is not available for this commodity."
    }
