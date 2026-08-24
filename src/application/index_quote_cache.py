"""Per-tick index quote batching shared by ticker and VIX payloads."""

import logging
from collections.abc import Callable
from typing import Any

from storage.caches import TickScopedDict

NSE_TICKER_SYMBOLS = ("NIFTY", "BANKNIFTY", "MIDCPNIFTY", "FINNIFTY")
BSE_TICKER_SYMBOLS = ("SENSEX", "BANKEX", "SENSEX50")
TICKER_SYMBOLS = NSE_TICKER_SYMBOLS + BSE_TICKER_SYMBOLS
VIX_CACHE_KEY = "India VIX"
VIX_TOKEN = "99926017"


class IndexQuoteCache:
    def __init__(self, market_data, *, active_provider, safe_number, warn, logger=None):
        self._market_data = market_data
        self._active_provider = active_provider
        self._safe_number = safe_number
        self._warn = warn
        self._logger = logger or logging.getLogger(__name__)
        self._cache = TickScopedDict()

    def get(self, symbol, default=None):
        return self._cache.get(symbol, default)

    def _normalize(self, row):
        if not row or "ltp" in row or "close" in row:
            return row
        if "last_price" not in row:
            return row
        return {
            key: self._safe_number(row.get(source))
            for key, source in {
                "ltp": "last_price", "close": "close", "open": "open",
                "high": "high", "low": "low", "volume": "volume",
                "oi": "oi", "net_change": "net_change", "pct_change": "pct_change",
            }.items()
        }

    def refresh(self):
        tokens = self._market_data.index_tokens()
        if not tokens:
            self._refresh_spot_quotes()
            return
        nse_pairs = [
            (symbol, tokens[symbol]["token"])
            for symbol in NSE_TICKER_SYMBOLS if symbol in tokens
        ]
        nse_pairs.append((VIX_CACHE_KEY, tokens.get("INDIAVIX", {}).get("token", VIX_TOKEN)))
        by_token = self._market_data.get_batch_quotes_by_token("NSE", nse_pairs, mode="FULL")
        nse_quotes = {
            symbol: self._normalize(by_token[str(token)])
            for symbol, token in nse_pairs if str(token) in by_token
        }
        bse_quotes = {}
        for symbol in BSE_TICKER_SYMBOLS:
            if symbol not in tokens:
                continue
            try:
                quote = self._normalize(self._market_data.get_spot_quote(symbol))
                if quote:
                    bse_quotes[symbol] = quote
            except Exception as error:
                self._logger.warning("BSE ticker spot quote %s failed: %s", symbol, error)
        self._cache.refill(nse_quotes, bse_quotes)

    def _refresh_spot_quotes(self):
        quotes = {}
        for symbol in TICKER_SYMBOLS:
            try:
                quote = self._market_data.get_spot_quote(symbol)
            except Exception as error:
                self._warn(f"spot:{symbol}", f"[{self._active_provider()}] spot quote {symbol} failed: {error}")
                quote = None
            if quote and quote.get("ltp"):
                quotes[symbol] = quote
        try:
            vix_quote = self._market_data.get_spot_quote("INDIA VIX")
        except Exception as error:
            self._warn("spot:INDIA VIX", f"[{self._active_provider()}] VIX spot quote failed: {error}")
            vix_quote = None
        nse_quotes = {symbol: quotes[symbol] for symbol in NSE_TICKER_SYMBOLS if symbol in quotes}
        if vix_quote and vix_quote.get("ltp"):
            nse_quotes[VIX_CACHE_KEY] = vix_quote
        bse_quotes = {symbol: quotes[symbol] for symbol in BSE_TICKER_SYMBOLS if symbol in quotes}
        self._cache.refill(nse_quotes, bse_quotes)
