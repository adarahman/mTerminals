from application.market_pipeline.quotes import (
    _BATCH_CACHE,
    fetch_sensex_ticker_smartapi,
    fetch_ticker_payload_smartapi,
    _index_pairs_by_exchange,
    _quotes_by_symbol,
)


def test_index_pairs_extract_tokens_and_group_by_exchange():
    tokens = {
        "NIFTY": {"exchange": "NSE", "token": "99926000"},
        "SENSEX": {"exchange": "BSE", "token": "99919000"},
    }

    pairs = _index_pairs_by_exchange(tokens, include_vix=True)

    assert pairs == {
        "NSE": [("NIFTY", "99926000"), ("India VIX", "99926017")],
        "BSE": [("SENSEX", "99919000")],
    }


def test_batch_rows_are_rekeyed_from_tokens_to_consumer_symbols():
    row = {"symbolToken": "99926017", "ltp": 13.5}

    quotes = _quotes_by_symbol(
        [("India VIX", "99926017")],
        {"99926017": row},
    )

    assert quotes == {"India VIX": row}


def test_default_ticker_payload_uses_cached_symbols():
    _BATCH_CACHE.refill(
        {"NIFTY": {"ltp": 25000.0, "close": 24900.0}}
    )

    payload = fetch_ticker_payload_smartapi()

    assert payload[0]["Symbol"] == "NIFTY"


def test_sensex_ticker_returns_one_quote_not_a_list():
    _BATCH_CACHE.refill(
        {"SENSEX": {"ltp": 80000.0, "close": 79800.0}}
    )

    quote = fetch_sensex_ticker_smartapi()

    assert quote["Symbol"] == "SENSEX"
