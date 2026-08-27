from execution.paper_trading import _instrument_key
from server.paper_portfolio import PaperPriceBook


def test_price_book_extracts_spot_future_and_option_legs():
    book = PaperPriceBook({}, _instrument_key)
    prices = book.build({
        "symbol": "NIFTY", "spot": 25000.0, "expiry": "01-Sep-2026",
        "futLTP": 25050.0,
        "chain": [{"strike": 25000, "ceLTP": 100.0, "peLTP": 90.0}],
    })
    assert prices[_instrument_key("NIFTY", "", None, "INDEX")] == 25000.0
    assert prices[_instrument_key("NIFTY", "01-Sep-2026", None, "FUT")] == 25050.0
    assert prices[_instrument_key("NIFTY", "01-Sep-2026", 25000, "CE")] == 100.0
    assert prices[_instrument_key("NIFTY", "01-Sep-2026", 25000, "PE")] == 90.0


def test_price_book_preserves_other_symbols_across_ticks():
    known = {_instrument_key("BANKNIFTY", "", None, "INDEX"): 58000.0}
    prices = PaperPriceBook(known, _instrument_key).build(
        {"symbol": "NIFTY", "spot": 25000.0}
    )
    assert len(prices) == 2
    assert prices[_instrument_key("BANKNIFTY", "", None, "INDEX")] == 58000.0
