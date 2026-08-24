from types import SimpleNamespace


def test_sensex_chain_uses_bse_fo_scrip_master_and_quotes(monkeypatch):
    """SENSEX must never be looked up in Kotak's NSE F&O master."""
    from brokers.kotak import market_data as kotak

    calls = []
    rows = [
        {
            "name": "SENSEX", "tradingsymbol": "SENSEXCE", "token": "101",
            "option_type": "CE", "strike": 80000, "expiry": "28-Aug-2025", "lot_size": 20,
        },
        {
            "name": "SENSEX", "tradingsymbol": "SENSEXPE", "token": "102",
            "option_type": "PE", "strike": 80000, "expiry": "28-Aug-2025", "lot_size": 20,
        },
    ]

    def load(segment):
        calls.append(("master", segment))
        return rows

    def quotes(*, instrument_tokens, quote_type):
        calls.append(("quotes", instrument_tokens))
        return [
            {"exchange_token": item["instrument_token"], "ltp": 100.0}
            for item in instrument_tokens
        ]

    monkeypatch.setattr(kotak, "_contracts_for", lambda _underlying: load("bse_fo"))
    monkeypatch.setattr(kotak, "_spot_quote", lambda _symbol: {"ltp": 80000.0})
    monkeypatch.setattr(kotak, "_session", SimpleNamespace(client=SimpleNamespace(quotes=quotes)))

    chain = kotak.get_atm_chain("SENSEX", "28-Aug-2025", strikes_around_atm=0)

    assert chain is not None
    assert calls[0] == ("master", "bse_fo")
    assert calls[1][1][0]["exchange_segment"] == "bse_fo"
    assert {row["type"] for row in chain["rows"]} == {"CE", "PE"}


def test_bfo_pipe_delimited_scrip_master_is_parsed():
    from brokers.kotak import market_data as kotak

    text = (
        "pSymbol|pSymbolName|pTrdSymbol|pInstType|pOptionType|pExpiryDate|"
        "dStrikePrice|lLotSize\n"
        "101|SENSEX|SENSEX26AUG80000CE|OPTIDX|CE|0|8000000|20\n"
    )

    rows = kotak._parse_fo_csv_text(text)

    assert len(rows) == 1
    assert rows[0]["name"] == "SENSEX"
    assert rows[0]["strike"] == 80000


def test_bfo_blank_metadata_is_inferred_from_trading_symbol():
    from brokers.kotak import market_data as kotak

    text = (
        "pSymbol,pSymbolName,pTrdSymbol,pInstType,pOptionType,pExpiryDate,dStrikePrice;,lLotSize\n"
        "101,SENSEX,SENSEX26AUG80000CE,,,0,8000000,20\n"
        "102,SENSEX,SENSEX26AUGFUT,,,0,0,20\n"
    )

    rows = kotak._parse_fo_csv_text(text)

    assert [row["option_type"] for row in rows] == ["CE", "FUT"]
