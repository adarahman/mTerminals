"""Read-side broker interface — market data only.

Scope note: this covers only the 7 functions actually called by
smartapi_pipeline_adapter.py, mTerminals_json.py, and ws_server_live.py
today (verified by AST, not grep — smartapi_feed_adapter.py's apparent
calls to get_atm_chain/list_expiries turned out to be inside a docstring,
not real code).

Order execution (place_order/get_order_book/get_funds) is deliberately
NOT here: those are imported in ws_server_live.py but never actually
called anywhere in the codebase — PaperTradingEngine (paper_trading.py)
is the only order path that's actually wired up and live. Don't guess
at an OrderExecution interface's shape until SmartAPI order placement
is for real; design it from real call sites the same way this one was.

SmartApiMarketData wraps brokers.smartapi_client's existing module-level
functions with zero logic changes — pure delegation, nothing here talks
to SmartAPI directly. To add a second provider, write another class
satisfying MarketData and swap the `market_data` instance below.
"""

from typing import Protocol, Optional

from brokers.smartapi_client import (
    list_expiries as _list_expiries,
    get_atm_chain as _get_atm_chain,
    find_option_token as _find_option_token,
    get_batch_quotes as _get_batch_quotes,
    get_spot_quote as _get_spot_quote,
    get_fno_underlyings as _get_fno_underlyings,
    INDEX_TOKENS as _INDEX_TOKENS,
)


class MarketData(Protocol):
    def list_expiries(self, underlying: str, exchange: str = "NFO") -> list:
        """Sorted expiry strings (SmartAPI format, e.g. '31JUL2026') for underlying."""
        ...

    def get_atm_chain(self, underlying: str, expiry_ddmmmyyyy: str,
                       strikes_around_atm: int = 10, exchange: str = "NFO") -> Optional[dict]:
        """{'underlying', 'spot', 'atm_strike', 'expiry', 'rows': [...]} or None."""
        ...

    def find_option_token(self, underlying: str, expiry_ddmmmyyyy: str,
                           strike, opt_type: str, exchange: str = "NFO") -> Optional[dict]:
        """{'tradingsymbol', 'token'} for one contract, or None if unresolved."""
        ...

    def get_batch_quotes(self, exchange: str, symbol_token_pairs: list,
                          mode: str = "FULL") -> dict:
        """Up to 50 (tradingsymbol, token) pairs -> dict keyed by tradingsymbol."""
        ...

    def get_spot_quote(self, underlying: str) -> Optional[dict]:
        """LTP + OHLC for one underlying, or None."""
        ...

    def get_fno_underlyings(self, force_refresh: bool = False) -> dict:
        """{'indices': [...], 'stocks': [...]}, alphabetically sorted."""
        ...

    def index_tokens(self) -> dict:
        """Read-only. {'NIFTY': {'token': ..., 'exchange': 'NSE'}, ...}."""
        ...


class SmartApiMarketData:
    """Thin adapter over brokers.smartapi_client — delegates as-is, no
    behavior changes. Existing call sites can switch their import from
    `from brokers.smartapi_client import X` to using the `market_data`
    singleton below with zero functional difference."""

    def list_expiries(self, underlying, exchange="NFO"):
        return _list_expiries(underlying, exchange=exchange)

    def get_atm_chain(self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
        return _get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm, exchange=exchange)

    def find_option_token(self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
        return _find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange=exchange)

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        return _get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_spot_quote(self, underlying):
        return _get_spot_quote(underlying)

    def get_fno_underlyings(self, force_refresh=False):
        return _get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        return _INDEX_TOKENS


market_data: MarketData = SmartApiMarketData()
