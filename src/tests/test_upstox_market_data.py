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


def test_commodity_rows_distinguish_contracts_sharing_group_name(monkeypatch):
    """Upstox MCX rows share name='GOLD' across GOLD/GOLDM/GOLDPETAL —
    the trading_symbol prefix is the only reliable discriminator."""
    from brokers.upstox import client

    dump = [
        {"name": "GOLD", "trading_symbol": "GOLD FUT 04 DEC 26",
         "instrument_type": "FUT", "expiry": 1796408999000},
        {"name": "GOLD", "trading_symbol": "GOLDM FUT 04 DEC 26",
         "instrument_type": "FUT", "expiry": 1796408999000},
        {"name": "GOLD", "trading_symbol": "GOLDPETAL FUT 30 NOV 26",
         "instrument_type": "FUT", "expiry": 1796063399000},
        {"name": "GOLD", "trading_symbol": "GOLD 124500 CE 26 FEB 27",
         "instrument_type": "CE", "expiry": 1803666599000},
        {"name": "GOLD", "trading_symbol": "GOLDM 125 CE 26 FEB 27",
         "instrument_type": "CE", "expiry": 1803666599000},
    ]
    monkeypatch.setattr(client, "_load_instrument_dump", lambda _exch: dump)

    rows = UpstoxMarketData._commodity_rows("GOLD", {"FUT"})
    assert [r["trading_symbol"] for r in rows] == ["GOLD FUT 04 DEC 26"]

    goldm = UpstoxMarketData._commodity_rows("GOLDM", {"FUT"})
    assert [r["trading_symbol"] for r in goldm] == ["GOLDM FUT 04 DEC 26"]

    ce = UpstoxMarketData._commodity_rows("GOLD", {"CE"})
    assert [r["trading_symbol"] for r in ce] == ["GOLD 124500 CE 26 FEB 27"]
