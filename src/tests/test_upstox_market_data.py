from brokers.upstox.market_data import UpstoxMarketData


def test_batch_quotes_use_resolved_upstox_instrument_key(monkeypatch):
    from brokers.upstox import client

    requested = []
    monkeypatch.setattr(
        client,
        "get_quotes",
        lambda keys: requested.extend(keys)
        or {
            "NSE_FO|123": {
                "instrument_token": "NSE_FO|123",
                "last_price": 25010.0,
            }
        },
    )
    monkeypatch.setattr(client, "index_instrument_key", lambda _symbol: None)

    quotes = UpstoxMarketData().get_batch_quotes(
        "NSE", [("NIFTY FUT 29 SEP 26", "NSE_FO|123")]
    )

    assert requested == ["NSE_FO|123"]
    assert quotes["NIFTY FUT 29 SEP 26"]["last_price"] == 25010.0


def test_batch_quotes_do_not_treat_foreign_numeric_token_as_upstox_key(monkeypatch):
    from brokers.upstox import client

    monkeypatch.setattr(
        client,
        "index_instrument_key",
        lambda _symbol: "NSE_INDEX|Nifty 50",
    )
    monkeypatch.setattr(
        client,
        "get_quotes",
        lambda keys: {keys[0]: {"instrument_token": keys[0], "last_price": 25000}},
    )

    quotes = UpstoxMarketData().get_batch_quotes(
        "NSE", [("NIFTY", "26000")]
    )

    assert quotes["NIFTY"]["instrument_token"] == "NSE_INDEX|Nifty 50"
