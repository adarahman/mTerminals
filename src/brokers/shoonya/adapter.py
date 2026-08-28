"""Broker-neutral market-data adapter for Shoonya."""
from datetime import datetime
from typing import Optional

from market.providers.nse_bse import _public_futures_quote


class ShoonyaMarketData:
    """Adapter over brokers.shoonya.market_data, implementing the same
    MarketData Protocol SmartApiMarketData/UpstoxMarketData do.

    Note this is a DIFFERENT class from brokers.shoonya.market_data's own
    ShoonyaMarketData: that one is broker-native and works in Shoonya's
    "DD-Mon-YYYY" expiry convention (see that module's docstring for why).
    This one wraps its module-level functions directly — same pattern
    UpstoxMarketData above uses over brokers.upstox.client — so that
    anything going through the `market_data` singleton still gets the
    Protocol's documented SmartAPI-format contract regardless of which
    broker is actually behind it. _to_shoonya/_to_ddmmmyyyy below do that
    conversion at the boundary.

    KNOWN GAP — same category as UpstoxMarketData's documented one: this
    class's get_batch_quotes()/get_batch_quotes_by_token() return
    brokers.shoonya.market_data's RAW per-contract row (Noren's own field
    abbreviations — lp/o/h/l/c/v/oi/nc/pc), not AngelOne's field names
    (ltp/netChange/...). server/app.py's
    IndexQuoteFetcher.provider() passes that raw row straight into
    _map_smartapi_quote(), which only understands AngelOne's shape — so
    that one call site needs a Shoonya-aware mapper before
    MARKET_DATA_PROVIDER=SHOONYA is safe to flip in production, same
    caveat UpstoxMarketData's docstring already gives for Upstox.
    """

    @staticmethod
    def _to_shoonya(expiry):
        """Normalize expiry to Shoonya DD-Mmm-YYYY format."""
        if isinstance(expiry, datetime):
            return expiry.strftime("%d-%b-%Y")

        value = str(expiry).strip()

        for fmt in ("%d-%b-%Y", "%d%b%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).strftime("%d-%b-%Y")
            except ValueError:
                continue

        raise ValueError(f"Unsupported expiry format: {expiry!r}")

    @staticmethod
    def _to_ddmmmyyyy(expiry_shoonya: Optional[str]) -> Optional[str]:
        """'31-Jul-2026' -> '31JUL2026' (upper-cased to match SmartAPI's
        ScripMaster convention)."""
        if not expiry_shoonya:
            return expiry_shoonya
        return datetime.strptime(expiry_shoonya, "%d-%b-%Y").strftime("%d%b%Y").upper()

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.shoonya.market_data import list_expiries as _sh_list_expiries

        return [
            self._to_ddmmmyyyy(e)
            for e in _sh_list_expiries(underlying, exchange=exchange)
        ]

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.shoonya.market_data import get_atm_chain as _sh_get_atm_chain

        chain = _sh_get_atm_chain(
            underlying,
            self._to_shoonya(expiry_ddmmmyyyy),
            strikes_around_atm,
            exchange=exchange,
        )
        if chain is None:
            return None
        chain = dict(chain)
        chain["expiry"] = (
            expiry_ddmmmyyyy  # hand back what the caller passed in, not the Shoonya-format one
        )
        return chain

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        from brokers.shoonya.market_data import (
            find_option_token as _sh_find_option_token,
        )

        return _sh_find_option_token(
            underlying,
            self._to_shoonya(expiry_ddmmmyyyy),
            strike,
            opt_type,
            exchange=exchange,
        )

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.shoonya.market_data import get_batch_quotes as _sh_get_batch_quotes

        return _sh_get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.shoonya.market_data import (
            get_batch_quotes_by_token as _sh_get_batch_quotes_by_token,
        )

        return _sh_get_batch_quotes_by_token(exchange, symbol_token_pairs, mode=mode)

    def get_spot_quote(self, underlying):
        from brokers.shoonya.market_data import get_spot_quote as _sh_get_spot_quote

        return _sh_get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # No broker-native FUTIDX resolution wired into this codebase for
        # Shoonya — always answers from the NSE/BSE public API, explicitly
        # flagged via FutSource so this is never a silent EQ/FUT split.
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.shoonya.market_data import (
            get_fno_underlyings as _sh_get_fno_underlyings,
        )

        return _sh_get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.shoonya.market_data import index_tokens as _sh_index_tokens

        return _sh_index_tokens()
