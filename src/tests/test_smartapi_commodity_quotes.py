from brokers.smartapi.market_data import SmartApiMarketData


def test_commodity_quote_resolves_mcx_future_and_calculates_change(monkeypatch):
    future = {
        "symbol": "CRUDEOILFUT",
        "token": "123",
        "expiry": "31DEC2099",
    }
    adapter = SmartApiMarketData()
    seen = []

    monkeypatch.setattr(
        "brokers.smartapi.instruments.get_commodity_futures_contract",
        lambda underlying, which: future,
    )

    def quotes(exchange, pairs, mode):
        seen.append((exchange, pairs, mode))
        return {
            "123": {
                "ltp": "110",
                "close": "100",
                "open": "102",
                "high": "112",
                "low": "99",
                "tradeVolume": "50",
                "opnInterest": "75",
            }
        }

    monkeypatch.setattr(adapter, "get_batch_quotes_by_token", quotes)

    result = adapter.get_commodity_futures_quote("CRUDEOIL")

    assert seen == [("MCX", [("CRUDEOILFUT", "123")], "FULL")]
    assert result["LTP"] == 110.0
    assert result["Change"] is None
    assert result["PctChange"] == 10.0
    assert result["Exchange"] == "MCX"
    assert result["Expiry"] == "31-Dec-2099"


def test_commodity_option_chain_quotes_only_nearby_calls_and_puts(monkeypatch):
    adapter = SmartApiMarketData()
    monkeypatch.setattr(
        adapter,
        "get_commodity_futures_quote",
        lambda underlying: {"LTP": 6000.0},
    )
    monkeypatch.setattr(
        "brokers.smartapi.instruments.get_commodity_option_contracts",
        lambda underlying, expiry, reference_price, strikes_around_atm: [
            {"strike": 6000.0, "type": "CE", "symbol": "CALL", "token": "10"},
            {"strike": 6000.0, "type": "PE", "symbol": "PUT", "token": "11"},
        ],
    )
    seen = []

    def quotes(exchange, pairs, mode):
        seen.append((exchange, pairs, mode))
        return {
            "10": {"ltp": "120", "opnInterest": "200", "tradeVolume": "30"},
            "11": {"ltp": "100", "opnInterest": "180", "tradeVolume": "25"},
        }

    monkeypatch.setattr(adapter, "get_batch_quotes_by_token", quotes)

    result = adapter.get_commodity_option_chain("CRUDEOIL", "31DEC2099")

    assert seen == [
        ("MCX", [("CALL", "10"), ("PUT", "11")], "FULL")
    ]
    assert result["atm_strike"] == 6000.0
    assert [row["type"] for row in result["rows"]] == ["CE", "PE"]
