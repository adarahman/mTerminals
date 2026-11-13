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
    payload = json.loads(response.text)
    assert payload["provider"] == "SMARTAPI"
    assert payload["symbol"] == "CRUDEOIL"
    assert payload["expiry"] == "31DEC2099"
    assert payload["expiries"] == ["31DEC2099"]
    assert payload["chain"] == chain
    analysis = payload["analysis"]
    assert analysis["verdict"] == "BULLISH"
    assert analysis["confidence"] > 0
    assert analysis["evidence_coverage"] == 43
    assert analysis["put_call_oi_ratio"] == 2.0
    assert analysis["call_oi"] == 100.0
    assert analysis["put_oi"] == 200.0
    assert analysis["call_oi_wall"] == 6000
    assert analysis["put_oi_wall"] == 6000
    contributors = {item["key"]: item for item in analysis["evidence_weights"]}
    assert contributors["pcr"]["weight"] == 25
    assert contributors["pcr"]["direction"] == "BULLISH"
    assert contributors["futures"]["weight"] == 18
    assert contributors["futures"]["direction"] == "BULLISH"
    assert contributors["oi_velocity"]["available"] is False
    assert "oi_velocity" in analysis["missing_inputs"]
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
