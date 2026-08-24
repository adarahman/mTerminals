"""Dashboard ticker-strip payload construction from normalized quotes."""

from collections.abc import Callable, Iterable
from typing import Any


def build_ticker_entry(symbol: str, quote: dict | None) -> dict | None:
    if not quote:
        return None
    last_price = quote.get("ltp")
    previous_close = quote.get("close")
    change = (
        round(last_price - previous_close, 2)
        if last_price is not None and previous_close
        else 0.0
    )
    change_percent = (
        round((change / previous_close) * 100.0, 2) if previous_close else 0.0
    )
    return {
        "Symbol": symbol,
        "BackendSymbol": symbol,
        "Last Price": last_price,
        "% Change": change_percent,
        "Change": change,
        "Prev Close": previous_close,
    }


def build_ticker_payload(
    symbols: Iterable[str],
    *,
    quote_lookup: Callable[[str], dict | None],
    safe_number: Callable[[Any], float | None],
) -> list[dict]:
    payload = []
    for symbol in symbols:
        raw_quote = quote_lookup(symbol)
        quote = (
            {
                "ltp": safe_number(raw_quote.get("ltp")),
                "close": safe_number(raw_quote.get("close")),
            }
            if raw_quote
            else None
        )
        entry = build_ticker_entry(symbol, quote)
        if entry:
            payload.append(entry)
    return payload
