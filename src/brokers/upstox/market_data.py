"""Broker-neutral market-data adapter for Upstox."""
from datetime import date, datetime
from typing import Optional

from market.providers.nse_bse import resolve_exchange_for_symbol


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class UpstoxMarketData:
    """Adapter over brokers.upstox.client, implementing the same
    MarketData protocol SmartApiMarketData does.

    Format translation: every method here keeps accepting/returning
    expiries in SmartAPI's DDMMMYYYY convention (e.g. '31JUL2026'),
    matching the Protocol's documented contract and every existing call
    site (option_chain_json.py, server/app.py, ...) — even though
    upstox_client.py itself works in Upstox's native 'YYYY-MM-DD'
    format. _to_iso/_to_ddmmmyyyy below do that conversion at the
    boundary so callers never need to know which broker is behind
    `market_data`.

    Identifier translation: Upstox has no numeric "token" the way
    SmartAPI/Shoonya do — instrument_key (e.g. 'NSE_FO|12345') is the
    only identifier Upstox exposes. Rather than bolt on a fake numeric
    token, this adapter reuses the Protocol's existing 'token' field/
    positional slot to carry the instrument_key string instead. That's
    safe for every current call site: they treat 'token' as an opaque
    broker-supplied handle to pass back into get_batch_quotes[_by_token]
    or place_order, never as something to parse or compare numerically.

    KNOWN GAP — do not point the `market_data` singleton at this class
    in production without checking server/app.py's
    fetch_index_quotes_smartapi_sync(): it calls
    market_data.get_batch_quotes_by_token() and passes the raw row
    straight into _map_smartapi_quote(), which parses AngelOne's own
    field names (ltp, netChange, ...). This adapter's
    get_batch_quotes_by_token() returns Upstox's raw v2/quotes row shape
    instead (last_price, net_change, ...), so that one call site would
    silently get None/garbage quotes until it's given an
    Upstox-equivalent mapper. Nothing else identified in this codebase's
    7 real market_data call sites (see this module's top-of-file scope
    note) has that same raw-shape assumption baked in.
    """

    @staticmethod
    def _to_iso(expiry_ddmmmyyyy: Optional[str]) -> Optional[str]:
        """'31JUL2026' or '31-Jul-2026' -> '2026-07-31'. None/empty passes
        through — some call sites resolve "current"/None expiry further
        downstream."""
        if not expiry_ddmmmyyyy:
            return expiry_ddmmmyyyy
        for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(expiry_ddmmmyyyy, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise ValueError(f"Unsupported expiry format: {expiry_ddmmmyyyy!r}")

    @staticmethod
    def _to_ddmmmyyyy(expiry_iso: Optional[str]) -> Optional[str]:
        """'2026-07-31' -> '31JUL2026' (upper-cased to match SmartAPI's
        ScripMaster convention, e.g. '31JUL2026' not '31Jul2026')."""
        if not expiry_iso:
            return expiry_iso
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d%b%Y"):
            try:
                return datetime.strptime(expiry_iso, fmt).strftime("%d%b%Y").upper()
            except ValueError:
                continue
        raise ValueError(f"Unsupported expiry format: {expiry_iso!r}")

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.upstox.client import list_expiries as _up_list_expiries

        return [
            self._to_ddmmmyyyy(e)
            for e in _up_list_expiries(underlying, exchange=exchange)
        ]

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.upstox.client import get_atm_chain as _up_get_atm_chain

        chain = _up_get_atm_chain(
            underlying,
            self._to_iso(expiry_ddmmmyyyy),
            strikes_around_atm,
            exchange=exchange,
        )
        if chain is None:
            return None
        chain = dict(chain)
        chain["expiry"] = (
            expiry_ddmmmyyyy  # hand back what the caller passed in, not the ISO form
        )
        return chain

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        from brokers.upstox.client import find_option_token as _up_find_option_token

        hit = _up_find_option_token(
            underlying,
            self._to_iso(expiry_ddmmmyyyy),
            strike,
            opt_type,
            exchange=exchange,
        )
        if hit is None:
            return None
        return {
            "tradingsymbol": hit.get("trading_symbol"),
            "token": hit.get("instrument_key"),
        }

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        # Upstox requires its own instrument_key values. The incoming
        # symbol_token_pairs may contain SmartAPI/shared tokens, so never
        # pass those tokens directly to Upstox.
        del exchange, mode

        from brokers.upstox.client import (
            get_quotes as _up_get_quotes,
            index_instrument_key,
        )

        if not symbol_token_pairs:
            return {}

        resolved = []

        for tradingsymbol, original_token in symbol_token_pairs:
            instrument_key = index_instrument_key(tradingsymbol)

            if instrument_key:
                resolved.append(
                    (tradingsymbol, original_token, instrument_key)
                )

        if not resolved:
            return {}

        quotes = _up_get_quotes(
            [instrument_key for _, _, instrument_key in resolved]
        )

        # Upstox responses commonly expose instrument_token containing the
        # actual instrument_key. Build a lookup from both response key and
        # instrument_token so either response shape works.
        by_key = {}

        for response_key, quote in quotes.items():
            by_key[str(response_key)] = quote

            instrument_token = quote.get("instrument_token")
            if instrument_token:
                by_key[str(instrument_token)] = quote

        results = {}

        for tradingsymbol, _original_token, instrument_key in resolved:
            quote = by_key.get(str(instrument_key))

            if quote:
                results[tradingsymbol] = quote

        return results


    def get_batch_quotes_by_token(
        self,
        exchange,
        symbol_token_pairs,
        mode="FULL",
    ):
        # Preserve the caller's original token/key contract while resolving
        # each symbol to an Upstox-specific instrument_key internally.
        del exchange, mode

        from brokers.upstox.client import (
            get_quotes as _up_get_quotes,
            index_instrument_key,
        )

        if not symbol_token_pairs:
            return {}

        resolved = []

        for tradingsymbol, original_token in symbol_token_pairs:
            instrument_key = index_instrument_key(tradingsymbol)

            if instrument_key:
                resolved.append(
                    (tradingsymbol, original_token, instrument_key)
                )

        if not resolved:
            return {}

        quotes = _up_get_quotes(
            [instrument_key for _, _, instrument_key in resolved]
        )

        by_key = {}

        for response_key, quote in quotes.items():
            by_key[str(response_key)] = quote

            instrument_token = quote.get("instrument_token")
            if instrument_token:
                by_key[str(instrument_token)] = quote

        results = {}

        for _tradingsymbol, original_token, instrument_key in resolved:
            quote = by_key.get(str(instrument_key))

            if quote:
                results[str(original_token)] = quote

        return results

    def get_spot_quote(self, underlying):
        from brokers.upstox.client import get_spot_quote as _up_get_spot_quote

        quote = _up_get_spot_quote(underlying)
        if not quote:
            return None
        ohlc = quote.get("ohlc") or {}
        return {
            "ltp": quote.get("last_price"),
            "open": ohlc.get("open") or quote.get("open"),
            "high": ohlc.get("high") or quote.get("high"),
            "low": ohlc.get("low") or quote.get("low"),
            "close": ohlc.get("close") or quote.get("close"),
        }

    def get_futures_quote(self, underlying, which="NEAR"):
        from brokers.upstox.client import _load_instrument_dump, _canonical_name as _up_canonical

        # Routed off the underlying (resolve_exchange_for_symbol), not the
        # exchange="NFO" default param broker_pipeline.fetch_futures_wide()'s
        # old Upstox branch keyed off — that always evaluated to "NSE" since
        # no real caller passes exchange="BFO"/"BSE", so SENSEX/BANKEX via
        # Upstox would have looked up futures against the wrong instrument
        # dump. Fixed here rather than carried forward.
        exchange_scope = resolve_exchange_for_symbol(underlying)
        data = _load_instrument_dump(exchange_scope)
        underlying_u = underlying.upper()
        name_u = _up_canonical(underlying, data) or underlying_u

        def _parse_expiry(row):
            raw = row.get("expiry")
            if raw in (None, "", 0):
                return None
            if isinstance(raw, (int, float)):
                try:
                    return datetime.utcfromtimestamp(raw / 1000)
                except (OverflowError, OSError, ValueError):
                    return None
            for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d%b%Y"):
                try:
                    return datetime.strptime(str(raw), fmt)
                except ValueError:
                    continue
            return None

        cands = [
            row
            for row in data
            if row.get("instrument_type") == "FUT"
            and (row.get("name") or "").upper() == name_u
        ]
        cands = [(row, _parse_expiry(row)) for row in cands]
        cands = [(row, exp) for row, exp in cands if exp is not None]
        if not cands:
            return None
        cands.sort(key=lambda pair: pair[1])
        today = datetime.combine(date.today(), datetime.min.time())
        live = [(row, exp) for row, exp in cands if exp >= today] or cands
        idx = {"NEAR": 0, "NEXT": 1, "FAR": 2}.get(which, 0)
        idx = min(idx, len(live) - 1)
        fut, exp = live[idx]

        quotes = self.get_batch_quotes(
            exchange_scope,
            [(fut.get("trading_symbol"), fut.get("instrument_key"))],
            mode="FULL",
        )
        q = quotes.get(fut.get("trading_symbol")) if quotes else None
        if not q:
            return None

        spot_quote = self.get_spot_quote(underlying)
        spot = spot_quote["ltp"] if spot_quote else 0.0
        ltp = _safe_float(q.get("last_price"))
        prev_close = _safe_float(q.get("close"))
        change = q.get("net_change")
        pct = q.get("percent_change")
        if pct is None and prev_close and ltp:
            pct = round(((ltp - prev_close) / prev_close) * 100.0, 2)

        exp_raw = fut.get("expiry")
        if isinstance(exp_raw, (int, float)):
            exp_str = datetime.utcfromtimestamp(exp_raw / 1000).strftime("%d-%b-%Y")
        else:
            exp_str = str(exp_raw)

        return {
            "Contract": fut.get("trading_symbol"),
            "Underlying": underlying,
            "Expiry": exp_str,
            "LTP": ltp,
            "Change": change,
            "PctChange": pct,
            "Open": q.get("open"),
            "High": q.get("high"),
            "Low": q.get("low"),
            "PrevClose": prev_close,
            "Volume": q.get("volume"),
            "Turnover": None,
            "OI": q.get("oi"),
            "Spot": spot,
            "Basis": round(ltp - spot, 2) if spot and ltp else None,
            "FutSource": "UPSTOX",
        }

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.upstox.client import INDEX_KEYS, _load_instrument_dump

        rows = []
        rows.extend(_load_instrument_dump("NSE", force_refresh=force_refresh))
        rows.extend(_load_instrument_dump("BSE", force_refresh=force_refresh))
        names = {
            (row.get("name") or "").strip().upper()
            for row in rows
            if row.get("instrument_type") in ("CE", "PE") and row.get("name")
        }
        index_names = set(INDEX_KEYS.keys())
        return {
            "indices": sorted(n for n in names if n in index_names),
            "stocks": sorted(n for n in names if n not in index_names),
        }

    def index_tokens(self):
        from brokers.upstox.client import INDEX_KEYS

        return {
            symbol: {"token": key, "exchange": key.split("_", 1)[0]}
            for symbol, key in INDEX_KEYS.items()
        }

