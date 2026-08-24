from market.quotes.ticker_payload import build_ticker_entry, build_ticker_payload


def _safe_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def test_build_ticker_entry_calculates_change_fields():
    assert build_ticker_entry("NIFTY", {"ltp": 25250.0, "close": 25000.0}) == {
        "Symbol": "NIFTY",
        "BackendSymbol": "NIFTY",
        "Last Price": 25250.0,
        "% Change": 1.0,
        "Change": 250.0,
        "Prev Close": 25000.0,
    }


def test_build_ticker_payload_skips_missing_quotes_and_normalizes_numbers():
    quotes = {
        "NIFTY": {"ltp": "25250", "close": "25000"},
        "SENSEX": None,
    }

    payload = build_ticker_payload(
        ["NIFTY", "SENSEX"],
        quote_lookup=quotes.get,
        safe_number=_safe_number,
    )

    assert [row["Symbol"] for row in payload] == ["NIFTY"]
    assert payload[0]["Last Price"] == 25250.0


def test_build_ticker_entry_handles_missing_close():
    entry = build_ticker_entry("BANKNIFTY", {"ltp": 57000.0, "close": None})
    assert entry["Change"] == 0.0
    assert entry["% Change"] == 0.0
