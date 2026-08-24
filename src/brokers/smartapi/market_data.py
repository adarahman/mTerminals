"""Broker-neutral market-data adapter for Angel One SmartAPI."""
from __future__ import annotations

from datetime import datetime
from typing import Optional


def _safe_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SmartApiMarketData:
    """Delegate the common market-data contract to the SmartAPI client."""

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.smartapi.client import list_expiries

        return list_expiries(underlying, exchange=exchange)

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.smartapi.client import get_atm_chain

        return get_atm_chain(
            underlying, expiry_ddmmmyyyy, strikes_around_atm, exchange=exchange
        )

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        from brokers.smartapi.client import find_option_token

        return find_option_token(
            underlying, expiry_ddmmmyyyy, strike, opt_type, exchange=exchange
        )

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.smartapi.client import get_batch_quotes

        return get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.smartapi.client import get_batch_quotes_by_token

        return get_batch_quotes_by_token(exchange, symbol_token_pairs, mode=mode)

    def get_spot_quote(self, underlying):
        from brokers.smartapi.client import get_spot_quote

        return get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        from brokers.smartapi.instruments import get_futures_contract

        future = get_futures_contract(underlying, exchange="NFO", which=which)
        if not future:
            return None
        quotes = self.get_batch_quotes(
            "NFO", [(future.get("symbol"), future.get("token"))], mode="FULL"
        )
        quote = quotes.get(future.get("symbol")) if quotes else None
        if not quote:
            return None
        ltp = _safe_float(quote.get("ltp"))
        previous_close = _safe_float(quote.get("close"))
        change = _safe_float(quote.get("netChange"))
        percent_change = _safe_float(quote.get("percentChange"))
        if not percent_change and previous_close and ltp:
            percent_change = round(((ltp - previous_close) / previous_close) * 100.0, 2)
        spot_quote = self.get_spot_quote(underlying)
        spot = spot_quote["ltp"] if spot_quote else 0.0
        return {
            "Contract": future.get("symbol"),
            "Underlying": underlying,
            "Expiry": datetime.strptime(future["expiry"], "%d%b%Y").strftime("%d-%b-%Y"),
            "LTP": ltp,
            "Change": change,
            "PctChange": percent_change,
            "Open": _safe_float(quote.get("open")),
            "High": _safe_float(quote.get("high")),
            "Low": _safe_float(quote.get("low")),
            "PrevClose": previous_close,
            "Volume": quote.get("volume"),
            "Turnover": None,
            "OI": quote.get("oi"),
            "Spot": spot,
            "Basis": round(ltp - spot, 2) if spot and ltp else None,
            "FutSource": "SMARTAPI",
        }

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.smartapi.client import get_fno_underlyings

        return get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.smartapi.client import INDEX_TOKENS

        return INDEX_TOKENS
