"""Short-lived cache of per-provider spot indications for the dashboard."""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_SYMBOL: str | None = None
_QUOTES: dict[str, dict] = {}


def store_provider_spot_quotes(symbol: str, quotes: dict[str, dict]) -> None:
    """Replace the cached provider quotes for the currently polled symbol."""
    global _SYMBOL, _QUOTES
    with _LOCK:
        _SYMBOL = str(symbol or "").strip().upper()
        _QUOTES = {
            str(provider).upper(): dict(quote)
            for provider, quote in quotes.items()
            if isinstance(quote, dict)
        }


def get_provider_spot_quotes(symbol: str) -> dict[str, dict]:
    """Return a copy of cached quotes only when they match the requested symbol."""
    with _LOCK:
        if _SYMBOL != str(symbol or "").strip().upper():
            return {}
        return {provider: dict(quote) for provider, quote in _QUOTES.items()}
