"""Broker-neutral market-data adapter for ICICI Breeze."""

from brokers.expiry_format import to_compact_expiry, to_dash_expiry
from market.providers.nse_bse import _public_futures_quote


class BreezeMarketData:
    """Adapter over brokers.breeze.market_data's module-level functions,
    implementing the same MarketData Protocol. NOTE: this is a DIFFERENT
    class from brokers.breeze.market_data's own BreezeMarketData — that one
    works in Breeze's native DD-Mon-YYYY expiry convention; this one wraps
    the module-level functions with the Protocol's documented SmartAPI-
    format boundary (DDMMMYYYY), the same pattern ShoonyaMarketData draws
    for its own DD-Mon-YYYY convention.

    Capability note: Breeze has REST market data but NO WebSocket tick
    client in this codebase (there is no brokers/breeze_ws_client.py), so
    PROVIDER_CAPABILITIES marks websocket=False and the dashboard reports
    POLLING for Breeze. BREEZE_API_SESSION expires daily and has no
    automated refresh path (see config.py / .env), so provider_status()
    reports SESSION_REQUIRED when it isn't populated."""

    @staticmethod
    def _to_dash(expiry):
        """'31JUL2026' | '31-Jul-2026' | '2026-07-31' -> '31-Jul-2026'
        (brokers.breeze.market_data's native expiry convention)."""
        return to_dash_expiry(expiry)

    @staticmethod
    def _to_ddmmmyyyy(expiry_dash):
        """'31-Jul-2026' -> '31JUL2026' (upper-cased, matching the
        Protocol's SmartAPI convention)."""
        return to_compact_expiry(expiry_dash)

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.breeze.market_data import list_expiries as _bz_list_expiries

        return [
            self._to_ddmmmyyyy(e)
            for e in _bz_list_expiries(underlying, exchange=exchange)
        ]

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.breeze.market_data import get_atm_chain as _bz_get_atm_chain

        chain = _bz_get_atm_chain(
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
        from brokers.breeze.market_data import (
            find_option_token as _bz_find_option_token,
        )

        return _bz_find_option_token(
            underlying,
            self._to_dash(expiry_ddmmmyyyy),
            strike,
            opt_type,
            exchange=exchange,
        )

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.breeze.market_data import (
            get_batch_quotes as _bz_get_batch_quotes,
        )

        return _bz_get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.breeze.market_data import (
            get_batch_quotes_by_token as _bz_get_batch_quotes_by_token,
        )

        return _bz_get_batch_quotes_by_token(
            exchange, symbol_token_pairs, mode=mode
        )

    def get_spot_quote(self, underlying):
        from brokers.breeze.market_data import get_spot_quote as _bz_get_spot_quote

        return _bz_get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # No broker-native FUTIDX resolution wired into this codebase for
        # Breeze — always answers from the NSE/BSE public API, explicitly
        # flagged via FutSource so this is never a silent EQ/FUT split.
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.breeze.market_data import (
            get_fno_underlyings as _bz_get_fno_underlyings,
        )

        return _bz_get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.breeze.market_data import index_tokens as _bz_index_tokens

        return _bz_index_tokens()
