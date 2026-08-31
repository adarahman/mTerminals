"""Broker-neutral market-data adapter for Kotak Neo."""

from brokers.expiry_format import to_compact_expiry, to_dash_expiry
from market.providers.nse_bse import _public_futures_quote


class KotakMarketData:
    """Adapter over brokers.kotak.market_data's module-level functions,
    implementing the same MarketData Protocol. Same pattern as
    ShoonyaMarketData/BreezeMarketData: the module-level functions work in
    the codebase's native DD-Mon-YYYY expiry convention; this wrapper
    converts to/from the Protocol's documented SmartAPI-format boundary
    (DDMMMYYYY).

    Capability note: Kotak has REST market data but NO WebSocket tick
    client in this codebase (there is no brokers/kotak_ws_client.py), so
    PROVIDER_CAPABILITIES marks websocket=False and the dashboard reports
    POLLING for Kotak. The SDK's two-step TOTP+MPIN login auto-runs from
    KOTAK_TOTP_SECRET/KOTAK_MPIN on first use (see
    brokers/kotak_client.py), so provider_status() reports UNAVAILABLE only
    when those credentials are missing."""

    @staticmethod
    def _to_dash(expiry):
        """'31JUL2026' | '31-Jul-2026' | '2026-07-31' -> '31-Jul-2026'
        (brokers.kotak.market_data's native expiry convention)."""
        return to_dash_expiry(expiry)

    @staticmethod
    def _to_ddmmmyyyy(expiry_dash):
        """'31-Jul-2026' -> '31JUL2026' (upper-cased, matching the
        Protocol's SmartAPI convention)."""
        return to_compact_expiry(expiry_dash)

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.kotak.market_data import list_expiries as _kk_list_expiries

        return [
            self._to_ddmmmyyyy(e)
            for e in _kk_list_expiries(underlying, exchange=exchange)
        ]

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.kotak.market_data import get_atm_chain as _kk_get_atm_chain

        chain = _kk_get_atm_chain(
            underlying,
            self._to_dash(expiry_ddmmmyyyy),
            strikes_around_atm,
            exchange=exchange,
        )
        if chain is None:
            return None
        chain = dict(chain)
        chain["expiry"] = expiry_ddmmmyyyy
        return chain

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        from brokers.kotak.market_data import (
            find_option_token as _kk_find_option_token,
        )

        return _kk_find_option_token(
            underlying,
            self._to_dash(expiry_ddmmmyyyy),
            strike,
            opt_type,
            exchange=exchange,
        )

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.kotak.market_data import (
            get_batch_quotes as _kk_get_batch_quotes,
        )

        return _kk_get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.kotak.market_data import (
            get_batch_quotes_by_token as _kk_get_batch_quotes_by_token,
        )

        return _kk_get_batch_quotes_by_token(
            exchange, symbol_token_pairs, mode=mode
        )

    def get_spot_quote(self, underlying):
        from brokers.kotak.market_data import get_spot_quote as _kk_get_spot_quote

        return _kk_get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # Kotak's own SDK resolves and quotes FUTIDX/FUTSTK natively (this
        # is the one non-SmartAPI provider that does) — especially
        # important for SENSEX/BANKEX, where the public BSE futures table
        # can have rows but omit LTP. Falls back to the NSE/BSE public API,
        # explicitly flagged via FutSource, only when Kotak's own
        # resolution comes back empty — never silently.
        from brokers.kotak.market_data import get_futures_quote as _kk_get_futures_quote

        quote = _kk_get_futures_quote(underlying, which=which)
        if quote:
            quote = dict(quote)
            quote["FutSource"] = "KOTAK"
            return quote
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.kotak.market_data import (
            get_fno_underlyings as _kk_get_fno_underlyings,
        )

        return _kk_get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.kotak.market_data import index_tokens as _kk_index_tokens

        return _kk_index_tokens()
