import asyncio
from collections import deque
import json
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from server.commodity_prices_api import CommodityPricesApi


class _Provider:
    def __init__(self, quote=None, expiries=None, chain=None):
        self.quote = quote
        self.expiries = expiries or []
        self.chain = chain
        self.requested = []

    def get_commodity_futures_quote(self, symbol, which="NEAR"):
        self.requested.append((symbol, which))
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
        "quote": {
            **quote,
            "FiveMinute": {"available": False, "window_minutes": 1},
        },
    }
    assert provider.requested == [("CRUDEOIL", "NEAR")]


def test_commodity_quote_selects_requested_future_slot():
    quote = {"Contract": "CRUDEOILJULFUT", "Expiry": "31-Jul-2026", "LTP": 7123.5}
    provider = _Provider(quote)
    api = CommodityPricesApi(
        market_data=lambda: provider,
        active_provider=lambda: "SMARTAPI",
    )
    request = SimpleNamespace(query={"symbol": "CRUDEOIL", "which": "NEXT"})

    response = asyncio.run(api.quote(request))

    assert response.status == 200
    assert json.loads(response.text)["quote"]["Expiry"] == "31-Jul-2026"
    assert provider.requested == [("CRUDEOIL", "NEXT")]


def _quote(ltp, contract="CRUDEOILFUT"):
    return {"Contract": contract, "LTP": ltp}


def _quote_ltp(response):
    return json.loads(response.text)["quote"]["LTP"]


def test_commodity_quote_holds_last_good_on_single_poll_spike():
    provider = _Provider(_quote(100.0))
    api = CommodityPricesApi(
        market_data=lambda: provider, active_provider=lambda: "SMARTAPI"
    )
    request = _request()

    assert _quote_ltp(asyncio.run(api.quote(request))) == 100.0

    # +30% in one ~1s poll — a bad tick, not a trade. Held at last good.
    provider.quote = _quote(130.0)
    assert _quote_ltp(asyncio.run(api.quote(request))) == 100.0

    # Back to normal — still consistent with last good.
    provider.quote = _quote(100.5)
    assert _quote_ltp(asyncio.run(api.quote(request))) == 100.5


def test_commodity_quote_accepts_persistent_new_level_after_confirm():
    provider = _Provider(_quote(100.0))
    api = CommodityPricesApi(
        market_data=lambda: provider, active_provider=lambda: "SMARTAPI"
    )
    request = _request()

    assert _quote_ltp(asyncio.run(api.quote(request))) == 100.0

    # A genuine gap persists across polls — accepted after ~3 confirms so
    # real fast moves are only delayed, never frozen out.
    provider.quote = _quote(106.0)
    assert _quote_ltp(asyncio.run(api.quote(request))) == 100.0
    assert _quote_ltp(asyncio.run(api.quote(request))) == 100.0
    assert _quote_ltp(asyncio.run(api.quote(request))) == 106.0
    assert _quote_ltp(asyncio.run(api.quote(request))) == 106.0


def test_commodity_quote_rejects_absurd_ltp_vs_prev_close_on_first_tick():
    # No last-good baseline yet — PrevClose sanity still catches a print
    # like 143624 against a ~345 contract.
    provider = _Provider({"Contract": "NATGASFUT", "LTP": 143624.0, "PrevClose": 345.0})
    api = CommodityPricesApi(
        market_data=lambda: provider, active_provider=lambda: "SMARTAPI"
    )

    response = asyncio.run(api.quote(_request("NATURALGAS")))

    # No baseline to fall back to — quote passes through, but the
    # frontend PrevClose guard rejects it for display.
    assert _quote_ltp(response) == 143624.0


def test_commodity_quote_holds_absurd_ltp_when_baseline_exists():
    provider = _Provider(_quote(345.0, "NATGASFUT"))
    api = CommodityPricesApi(
        market_data=lambda: provider, active_provider=lambda: "SMARTAPI"
    )
    request = _request("NATURALGAS")

    assert _quote_ltp(asyncio.run(api.quote(request))) == 345.0

    provider.quote = {"Contract": "NATGASFUT", "LTP": 143624.0, "PrevClose": 344.0}
    assert _quote_ltp(asyncio.run(api.quote(request))) == 345.0


def test_commodity_quote_rebaselines_on_contract_change():
    provider = _Provider(_quote(100.0, "CRUDEOILFEBFUT"))
    api = CommodityPricesApi(
        market_data=lambda: provider, active_provider=lambda: "SMARTAPI"
    )
    request = _request()

    assert _quote_ltp(asyncio.run(api.quote(request))) == 100.0

    # Roll to a differently-priced contract — not a "spike", new baseline.
    provider.quote = _quote(250.0, "CRUDEOILMARFUT")
    assert _quote_ltp(asyncio.run(api.quote(request))) == 250.0


def test_commodity_quote_rejects_invalid_future_slot():
    provider = _Provider({"LTP": 1})
    api = CommodityPricesApi(market_data=lambda: provider)
    request = SimpleNamespace(query={"symbol": "CRUDEOIL", "which": "MONTHLY"})

    response = asyncio.run(api.quote(request))

    assert response.status == 400
    assert json.loads(response.text) == {
        "error": "Futures slot must be NEAR, NEXT, or FAR."
    }


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
    assert "no MCX futures quote adapter" in payload["error"]


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
    assert payload["chain"]["rows"] == chain["rows"]
    assert payload["chain"]["flow_baseline"] == "previous_close"
    assert payload["chain"]["flow_baseline_status"] == "unsupported"
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


def test_commodity_option_chain_exposes_previous_close_capital_flow():
    chain = {
        "atm_strike": 6000,
        "rows": [
            {"strike": 6000, "type": "CE", "token": "101", "oi": 110, "ltp": 5},
            {"strike": 6000, "type": "PE", "token": "102", "oi": 180, "ltp": 8},
        ],
    }
    provider = _Provider(
        expiries=["31DEC2099"],
        chain=chain,
    )
    api = CommodityPricesApi(market_data=lambda: provider)
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    api._previous_close_oi = {
        ("CRUDEOIL", "31DEC2099", "101", today): 100,
        ("CRUDEOIL", "31DEC2099", "102", today): 200,
    }

    response = asyncio.run(api.option_chain(_request()))

    response_chain = json.loads(response.text)["chain"]
    rows = response_chain["rows"]
    assert rows[0]["oi_change"] == 10
    assert rows[0]["capital_flow"] == 50
    assert rows[1]["oi_change"] == -20
    assert rows[1]["capital_flow"] == -160
    assert response_chain["flow_baseline"] == "previous_close"
    assert response_chain["flow_baseline_status"] == "ready"
    assert all("token" not in row for row in rows)


def test_commodity_option_chain_rejects_unavailable_expiry():
    provider = _Provider(expiries=["31DEC2099"])
    api = CommodityPricesApi(market_data=lambda: provider)
    request = SimpleNamespace(query={"symbol": "CRUDEOIL", "expiry": "01JAN2099"})

    response = asyncio.run(api.option_chain(request))

    assert response.status == 400
    assert json.loads(response.text) == {
        "error": "Expiry is not available for this commodity."
    }


def test_commodity_quote_reports_mcx_unsupported_provider():
    provider = _Provider({"LTP": 1})
    api = CommodityPricesApi(
        market_data=lambda: provider,
        active_provider=lambda: "BREEZE",
    )

    response = asyncio.run(api.quote(_request()))

    assert response.status == 501
    payload = json.loads(response.text)
    assert payload["provider"] == "BREEZE"
    assert "does not provide MCX market data" in payload["error"]
    assert provider.requested == []


def test_commodity_option_chain_reports_mcx_unsupported_provider():
    provider = _Provider(expiries=["31DEC2099"])
    api = CommodityPricesApi(
        market_data=lambda: provider,
        active_provider=lambda: "BREEZE",
    )

    response = asyncio.run(api.option_chain(_request()))

    assert response.status == 501
    payload = json.loads(response.text)
    assert payload["provider"] == "BREEZE"
    assert "does not provide MCX market data" in payload["error"]
    assert provider.requested == []


def test_commodity_oi_velocity_computes_window_deltas():
    api = CommodityPricesApi(market_data=lambda: _Provider())

    def rows(ce_oi, pe_oi):
        return [
            {"strike": 6000, "type": "CE", "oi": ce_oi, "ltp": 5},
            {"strike": 6000, "type": "PE", "oi": pe_oi, "ltp": 8},
        ]

    now = time.monotonic()
    api._chain_history[("CRUDEOIL", "31DEC2099")] = deque(
        [
            (now - 360, rows(100, 200)),
            (now - 60, rows(110, 180)),
            (now, rows(130, 150)),
        ],
        maxlen=400,
    )

    blocks = api._oi_velocity("CRUDEOIL", "31DEC2099")
    by_window = {block["window"]: block for block in blocks}

    five = by_window[5]["rows"]
    assert len(five) == 1
    row = five[0]
    # Baseline is the 360s-old snapshot (only one >= 150s old); the delta
    # is scaled to the nominal 300s window: (130-100) * 300/360 = 25.
    assert row["ceDOI"] == 25
    assert row["peDOI"] == -41.67
    assert row["signal"] == "CE Build / PE Unwind"
    assert row["ceNow"] == 130
    assert row["peNow"] == 150

    # No snapshot is old enough for the 15m/30m windows yet.
    assert by_window[15]["rows"] == []
    assert by_window[30]["rows"] == []


def test_commodity_oi_velocity_empty_without_history():
    api = CommodityPricesApi(market_data=lambda: _Provider())
    blocks = api._oi_velocity("CRUDEOIL", "31DEC2099")
    assert [block["window"] for block in blocks] == [5, 15, 30]
    assert all(block["rows"] == [] for block in blocks)


def test_commodity_option_chain_attaches_black76_greeks():
    from analytics.commodity_greeks import black76_price, years_to_expiry

    t = years_to_expiry("31DEC2099")
    rows = [
        {"strike": k, "type": side, "oi": 100,
         "ltp": round(black76_price(6000.0, k, t, 0.3, "C" if side == "CE" else "P"), 2)}
        for k in (5900, 6000, 6100) for side in ("CE", "PE")
    ]
    chain = {"spot": 6000.0, "atm_strike": 6000, "lot_size": 100, "rows": rows}
    provider = _Provider(expiries=["31DEC2099"], chain=chain)
    api = CommodityPricesApi(market_data=lambda: provider, active_provider=lambda: "SMARTAPI")

    response_chain = json.loads(asyncio.run(api.option_chain(_request())).text)["chain"]

    assert abs(response_chain["atm_iv"] - 30) < 0.5
    assert [g["strike"] for g in response_chain["greeks"]] == [5900, 6000, 6100]
    assert all("iv" in row for row in response_chain["rows"])
