"""Broker-neutral market-data adapter for Zerodha Kite."""
from market.providers.nse_bse import _public_futures_quote


class KiteMarketData:
    """Adapter over brokers.kite.client, implementing the same MarketData
    Protocol SmartApiMarketData/UpstoxMarketData do.

    Kite's own module already speaks SmartAPI's DDMMMYYYY expiry convention
    (see kite_client.list_expiries's docstring), so — unlike the Upstox/
    Shoonya adapters — no format translation is needed at this boundary;
    this class is a thin pass-through delegating to kite_client's
    module-level functions.

    Capability note: Kite has REST market data but NO WebSocket tick client
    in this codebase (there is no brokers/kite_ws_client.py), so
    PROVIDER_CAPABILITIES marks websocket=False and the dashboard reports
    POLLING for Kite — matching how its data is actually delivered, rather
    than claiming a live stream that doesn't exist."""

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.kite.client import list_expiries as _k_list_expiries

        return _k_list_expiries(underlying, exchange=exchange)

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.kite.client import get_atm_chain as _k_get_atm_chain

        chain = _k_get_atm_chain(
            underlying,
            expiry_ddmmmyyyy,
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
        from brokers.kite.client import find_option_token as _k_find_option_token

        return _k_find_option_token(
            underlying, expiry_ddmmmyyyy, strike, opt_type, exchange=exchange
        )

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        del mode
        from brokers.kite.client import get_quotes as _k_get_quotes

        if not symbol_token_pairs:
            return {}
        keys = [f"{exchange}:{tradingsymbol}" for tradingsymbol, _ in symbol_token_pairs]
        raw = _k_get_quotes(keys)
        return {
            tradingsymbol: raw[key]
            for (tradingsymbol, _), key in zip(symbol_token_pairs, keys)
            if raw.get(key)
        }

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        by_symbol = self.get_batch_quotes(exchange, symbol_token_pairs, mode=mode)
        return {
            str(token): by_symbol[tradingsymbol]
            for tradingsymbol, token in symbol_token_pairs
            if tradingsymbol in by_symbol
        }

    def get_spot_quote(self, underlying):
        from brokers.kite.client import get_spot_quote as _k_get_spot_quote

        return _k_get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # No broker-native FUTIDX resolution wired into this codebase for
        # Kite — always answers from the NSE/BSE public API, explicitly
        # flagged via FutSource so this is never a silent EQ/FUT split.
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        # Public ScripMaster universe (same source kite_client's instrument
        # dump comes from, minus the broker session requirement).
        from brokers.smartapi.instruments import (
            get_fno_underlyings as _public_fno_underlyings,
        )

        return _public_fno_underlyings(refresh=force_refresh)

    def index_tokens(self):
        # Kite has no index token model — index quotes go through
        # get_spot_quote() (see IndexQuoteFetcher.provider()'s
        # provider-aware branch for KITE/BREEZE).
        return {}


# ── ICICI Breeze provider ────────────────────────────────────────────────
