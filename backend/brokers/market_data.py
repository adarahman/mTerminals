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
to SmartAPI directly. BreezeMarketData (brokers/breeze_market_data.py) is
the second implementation of this Protocol — selected via
MARKET_DATA_PROVIDER, independently of EXECUTION_BROKER (see
config.py's comment on why those two selectors are separate).
"""

from typing import Protocol, Optional

# Deliberately NOT imported at module level: brokers.smartapi_client
# imports the SmartApi SDK itself at its own module top level, which
# would force smartapi-python to be installed even for a Breeze-only
# deployment (MARKET_DATA_PROVIDER=BREEZE) that never touches this
# class. SmartApiMarketData's methods below import it lazily, on first
# real use, instead.
def _smartapi():
    from brokers import smartapi_client
    return smartapi_client


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

    def get_batch_quotes_by_token(self, exchange: str, symbol_token_pairs: list,
                                   mode: str = "FULL") -> dict:
        """Same request as get_batch_quotes(), but dict keyed by str(symbolToken)
        instead of Angel's tradingsymbol display name — use when the caller
        needs to re-key back to its own symbol names, not Angel's."""
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
        return _smartapi().list_expiries(underlying, exchange=exchange)

    def get_atm_chain(self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
        return _smartapi().get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm, exchange=exchange)

    def find_option_token(self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
        return _smartapi().find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange=exchange)

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        return _smartapi().get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        return _smartapi().get_batch_quotes_by_token(exchange, symbol_token_pairs, mode=mode)

    def get_spot_quote(self, underlying):
        return _smartapi().get_spot_quote(underlying)

    def get_fno_underlyings(self, force_refresh=False):
        return _smartapi().get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        return _smartapi().INDEX_TOKENS


def _build_market_data() -> MarketData:
    try:  # ws_server_live adds backend/ to sys.path; package-level tests do not.
        from config import settings
    except ModuleNotFoundError:  # pragma: no cover - depends on launch style
        from backend.config import settings

    provider = settings.market_data_provider
    if provider == "BREEZE":
        from brokers.breeze_market_data import BreezeMarketData
        return BreezeMarketData()
    if provider == "SMARTAPI":
        return SmartApiMarketData()
    raise RuntimeError(
        f"MARKET_DATA_PROVIDER must be SMARTAPI or BREEZE, got {provider!r}"
    )


market_data: MarketData = _build_market_data()
