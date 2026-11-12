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

    def get_batch_quotes_by_token(
        self, exchange, symbol_token_pairs, mode="FULL", **retry_policy
    ):
        from brokers.smartapi.client import get_batch_quotes_by_token

        return get_batch_quotes_by_token(
            exchange, symbol_token_pairs, mode=mode, **retry_policy
        )

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
            "Volume": _safe_float(quote.get("tradeVolume")),
            "Turnover": None,
            "OI": _safe_float(quote.get("opnInterest")),
            "Spot": spot,
            "Basis": round(ltp - spot, 2) if spot and ltp else None,
            "FutSource": "SMARTAPI",
        }

    def get_commodity_futures_quote(self, underlying, which="NEAR"):
        from brokers.smartapi.instruments import get_commodity_futures_contract

        future = get_commodity_futures_contract(underlying, which=which)
        if not future:
            return None
        token = str(future.get("token") or "")
        quotes = self.get_batch_quotes_by_token(
            "MCX", [(future.get("symbol"), token)], mode="FULL"
        )
        quote = quotes.get(token) if quotes else None
        if not quote:
            return None
        ltp = _safe_float(quote.get("ltp"))
        if ltp is None:
            return None
        previous_close = _safe_float(quote.get("close"))
        change = _safe_float(quote.get("netChange"))
        percent_change = _safe_float(quote.get("percentChange"))
        if percent_change is None and previous_close and ltp:
            percent_change = round(
                ((ltp - previous_close) / previous_close) * 100.0, 2
            )
        return {
            "Contract": future.get("symbol"),
            "Underlying": underlying,
            "Exchange": "MCX",
            "Expiry": datetime.strptime(
                future["expiry"], "%d%b%Y"
            ).strftime("%d-%b-%Y"),
            "LTP": ltp,
            "Change": change,
            "PctChange": percent_change,
            "Open": _safe_float(quote.get("open")),
            "High": _safe_float(quote.get("high")),
            "Low": _safe_float(quote.get("low")),
            "PrevClose": previous_close,
            "Volume": _safe_float(quote.get("tradeVolume")),
            "OI": _safe_float(quote.get("opnInterest")),
            "FutSource": "SMARTAPI",
        }

    def get_commodity_option_expiries(self, underlying):
        from brokers.smartapi.instruments import get_commodity_option_expiries

        return get_commodity_option_expiries(underlying)

    def get_commodity_option_chain(
        self, underlying, expiry, strikes_around_atm=10
    ):
        from brokers.smartapi.instruments import get_commodity_option_contracts

        future = self.get_commodity_futures_quote(underlying)
        if not future:
            return None
        contracts = get_commodity_option_contracts(
            underlying,
            expiry,
            future["LTP"],
            strikes_around_atm=strikes_around_atm,
        )
        if not contracts:
            return None
        pairs = [(row["symbol"], row["token"]) for row in contracts]
        quotes = self.get_batch_quotes_by_token("MCX", pairs, mode="FULL")
        rows = []
        for contract in contracts:
            quote = quotes.get(str(contract["token"]))
            if not quote:
                continue
            rows.append(
                {
                    "strike": contract["strike"],
                    "type": contract["type"],
                    "ltp": _safe_float(quote.get("ltp")),
                    "oi": _safe_float(quote.get("opnInterest")),
                    "volume": _safe_float(quote.get("tradeVolume")),
                    "net_change": _safe_float(quote.get("netChange")),
                    "pct_change": _safe_float(quote.get("percentChange")),
                }
            )
        rows.sort(key=lambda row: (row["strike"], row["type"]))
        return {
            "underlying": underlying.upper(),
            "spot": future["LTP"],
            "future_change_pct": future.get("PctChange"),
            "atm_strike": min(
                (row["strike"] for row in contracts),
                key=lambda strike: abs(strike - future["LTP"]),
            ),
            "expiry": expiry,
            "rows": rows,
        }

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.smartapi.client import get_fno_underlyings

        return get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.smartapi.client import get_index_tokens

        return get_index_tokens()
