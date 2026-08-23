"""Read-side broker interface — market data only.

Scope note: this covers only the 7 functions actually called by
broker_pipeline.py, mTerminals_json.py, and ws_server_live.py
today (verified by AST, not grep — tick_pipeline.py's apparent
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

import logging
import time
from datetime import date, datetime
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

from config import settings as _md_settings
from brokers.logging import broker_event
from brokers.provider_registry import (
    PROVIDER_KEYS,
    normalize_provider,
    provider_capabilities,
    provider_display_names,
)


class MarketData(Protocol):
    def list_expiries(self, underlying: str, exchange: str = "NFO") -> list:
        """Sorted expiry strings (SmartAPI format, e.g. '31JUL2026') for underlying."""
        ...

    def get_atm_chain(
        self,
        underlying: str,
        expiry_ddmmmyyyy: str,
        strikes_around_atm: int = 10,
        exchange: str = "NFO",
    ) -> Optional[dict]:
        """{'underlying', 'spot', 'atm_strike', 'expiry', 'rows': [...]} or None."""
        ...

    def find_option_token(
        self,
        underlying: str,
        expiry_ddmmmyyyy: str,
        strike,
        opt_type: str,
        exchange: str = "NFO",
    ) -> Optional[dict]:
        """{'tradingsymbol', 'token'} for one contract, or None if unresolved."""
        ...

    def get_batch_quotes(
        self, exchange: str, symbol_token_pairs: list, mode: str = "FULL"
    ) -> dict:
        """Up to 50 (tradingsymbol, token) pairs -> dict keyed by tradingsymbol."""
        ...

    def get_batch_quotes_by_token(
        self, exchange: str, symbol_token_pairs: list, mode: str = "FULL"
    ) -> dict:
        """Same request as get_batch_quotes(), but dict keyed by str(symbolToken)
        instead of Angel's tradingsymbol display name — use when the caller
        needs to re-key back to its own symbol names, not Angel's."""
        ...

    def get_spot_quote(self, underlying: str) -> Optional[dict]:
        """LTP + OHLC for one underlying, or None."""
        ...

    def get_futures_quote(self, underlying: str, which: str = "NEAR") -> Optional[dict]:
        """One normalized FUT quote (same row shape broker_pipeline's
        fetch_futures_wide() has always returned: Contract/Underlying/
        Expiry/LTP/Change/PctChange/Open/High/Low/PrevClose/Volume/OI/
        Spot/Basis), or None if unresolved.

        which: NEAR / NEXT / FAR — 1st/2nd/3rd soonest listed monthly
        contract.

        Always includes 'FutSource', the provider that actually supplied
        this quote. This is NOT guaranteed to equal the active EQ
        provider: Shoonya/Kite/Breeze have no broker-native FUTIDX
        resolution in this codebase and always answer from the NSE/BSE
        public API (FutSource="NSE_BSE"); Kotak answers natively but
        falls back the same way when its own resolution comes back
        empty (e.g. SENSEX gaps). This flag exists specifically so a
        caller can detect and surface an EQ/FUT source mismatch instead
        of it being silent — compare against get_active_provider()."""
        ...

    def get_fno_underlyings(self, force_refresh: bool = False) -> dict:
        """{'indices': [...], 'stocks': [...]}, alphabetically sorted."""
        ...

    def index_tokens(self) -> dict:
        """Read-only. {'NIFTY': {'token': ..., 'exchange': 'NSE'}, ...}."""
        ...


def _public_futures_quote(underlying: str, which: str = "NEAR") -> Optional[dict]:
    """Shared NSE/BSE public-API futures fallback.

    Used directly by providers with no broker-native FUTIDX resolution in
    this codebase (Shoonya/Kite/Breeze/NSE_BSE), and as an explicit
    fallback by providers whose own resolution can come back empty
    (Kotak — see KotakMarketData.get_futures_quote). Always stamps
    FutSource="NSE_BSE" so this is never a silent EQ/FUT provider split —
    see MarketData.get_futures_quote's docstring."""
    from market_api import fetch_public_futures

    frame = fetch_public_futures(underlying, which=which)
    if frame is None or frame.empty:
        return None
    row = frame.iloc[0].to_dict()
    row["FutSource"] = "NSE_BSE"
    return row


class SmartApiMarketData:
    """Thin adapter over brokers.smartapi_client — delegates as-is, no
    behavior changes. Existing call sites can switch their import from
    `from brokers.smartapi_client import X` to using the `market_data`
    singleton below with zero functional difference."""

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.smartapi_client import list_expiries as _list_expiries

        return _list_expiries(underlying, exchange=exchange)

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.smartapi_client import get_atm_chain as _get_atm_chain

        return _get_atm_chain(
            underlying, expiry_ddmmmyyyy, strikes_around_atm, exchange=exchange
        )

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        from brokers.smartapi_client import find_option_token as _find_option_token

        return _find_option_token(
            underlying, expiry_ddmmmyyyy, strike, opt_type, exchange=exchange
        )

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.smartapi_client import get_batch_quotes as _get_batch_quotes

        return _get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.smartapi_client import (
            get_batch_quotes_by_token as _get_batch_quotes_by_token,
        )

        return _get_batch_quotes_by_token(exchange, symbol_token_pairs, mode=mode)

    def get_spot_quote(self, underlying):
        from brokers.smartapi_client import get_spot_quote as _get_spot_quote

        return _get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # Lazy import: broker_pipeline imports `market_data` at module
        # level, so importing it back at module level here would be
        # circular. _get_futures_contract is the same scrip-master FUTIDX
        # resolver fetch_futures_wide() already used inline for this
        # provider — reused here rather than duplicated a second time.
        from broker_pipeline import _get_futures_contract, _from_smartapi_expiry

        fut = _get_futures_contract(underlying, exchange="NFO", which=which)
        if not fut:
            return None
        quotes = self.get_batch_quotes(
            "NFO", [(fut.get("symbol"), fut.get("token"))], mode="FULL"
        )
        q = quotes.get(fut.get("symbol")) if quotes else None
        if not q:
            return None
        ltp = _safe_float(q.get("ltp"))
        prev_close = _safe_float(q.get("close"))
        change = _safe_float(q.get("netChange"))
        pct = _safe_float(q.get("percentChange"))
        if not pct and prev_close and ltp:
            pct = round(((ltp - prev_close) / prev_close) * 100.0, 2)
        spot_quote = self.get_spot_quote(underlying)
        spot = spot_quote["ltp"] if spot_quote else 0.0
        return {
            "Contract": fut.get("symbol"),
            "Underlying": underlying,
            "Expiry": _from_smartapi_expiry(fut["expiry"]),
            "LTP": ltp,
            "Change": change,
            "PctChange": pct,
            "Open": _safe_float(q.get("open")),
            "High": _safe_float(q.get("high")),
            "Low": _safe_float(q.get("low")),
            "PrevClose": prev_close,
            "Volume": q.get("volume"),
            "Turnover": None,
            "OI": q.get("oi"),
            "Spot": spot,
            "Basis": round(ltp - spot, 2) if spot and ltp else None,
            "FutSource": "SMARTAPI",
        }

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.smartapi_client import get_fno_underlyings as _get_fno_underlyings

        return _get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.smartapi_client import INDEX_TOKENS as _INDEX_TOKENS

        return _INDEX_TOKENS


class UpstoxMarketData:
    """Adapter over brokers.upstox_client, implementing the same
    MarketData protocol SmartApiMarketData does.

    Format translation: every method here keeps accepting/returning
    expiries in SmartAPI's DDMMMYYYY convention (e.g. '31JUL2026'),
    matching the Protocol's documented contract and every existing call
    site (option_chain_json.py, ws_server_live.py, ...) — even though
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
    in production without checking ws_server_live.py's
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
        from brokers.upstox_client import list_expiries as _up_list_expiries

        return [
            self._to_ddmmmyyyy(e)
            for e in _up_list_expiries(underlying, exchange=exchange)
        ]

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.upstox_client import get_atm_chain as _up_get_atm_chain

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
        from brokers.upstox_client import find_option_token as _up_find_option_token

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
        # exchange is unused: Upstox's instrument_key already encodes segment
        # (e.g. 'NSE_FO|...'), unlike SmartAPI where exchange is a separate
        # request parameter alongside bare numeric tokens.
        del exchange
        from brokers.upstox_client import get_quotes as _up_get_quotes

        if not symbol_token_pairs:
            return {}
        instrument_keys = [token for _, token in symbol_token_pairs]
        quotes = _up_get_quotes(instrument_keys)  # keyed by Upstox's own composite key
        by_instrument_key = {q.get("instrument_token"): q for q in quotes.values()}
        return {
            tradingsymbol: by_instrument_key[token]
            for tradingsymbol, token in symbol_token_pairs
            if token in by_instrument_key
        }

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        # See class docstring's KNOWN GAP note before wiring this into any
        # call site that assumes SmartAPI's raw quote field names.
        del exchange
        from brokers.upstox_client import get_quotes as _up_get_quotes

        if not symbol_token_pairs:
            return {}
        instrument_keys = [token for _, token in symbol_token_pairs]
        quotes = _up_get_quotes(instrument_keys)
        results = {}
        for q in quotes.values():
            ik = q.get("instrument_token")
            if ik:
                results[str(ik)] = q
        return results

    def get_spot_quote(self, underlying):
        from brokers.upstox_client import get_spot_quote as _up_get_spot_quote

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
        from brokers.upstox_client import _load_instrument_dump, _canonical_name as _up_canonical

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
        from brokers.upstox_client import INDEX_KEYS, _load_instrument_dump

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
        from brokers.upstox_client import INDEX_KEYS

        return {
            symbol: {"token": key, "exchange": key.split("_", 1)[0]}
            for symbol, key in INDEX_KEYS.items()
        }


class ShoonyaMarketData:
    """Adapter over brokers.shoonya_market_data, implementing the same
    MarketData Protocol SmartApiMarketData/UpstoxMarketData do.

    Note this is a DIFFERENT class from brokers.shoonya_market_data's own
    ShoonyaMarketData: that one is broker-native and works in Shoonya's
    "DD-Mon-YYYY" expiry convention (see that module's docstring for why).
    This one wraps its module-level functions directly — same pattern
    UpstoxMarketData above uses over brokers.upstox_client — so that
    anything going through the `market_data` singleton still gets the
    Protocol's documented SmartAPI-format contract regardless of which
    broker is actually behind it. _to_shoonya/_to_ddmmmyyyy below do that
    conversion at the boundary.

    KNOWN GAP — same category as UpstoxMarketData's documented one: this
    class's get_batch_quotes()/get_batch_quotes_by_token() return
    brokers.shoonya_market_data's RAW per-contract row (Noren's own field
    abbreviations — lp/o/h/l/c/v/oi/nc/pc), not AngelOne's field names
    (ltp/netChange/...). ws_server_live.py's
    fetch_index_quotes_smartapi_sync() passes that raw row straight into
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
        from brokers.shoonya_market_data import list_expiries as _sh_list_expiries

        return [
            self._to_ddmmmyyyy(e)
            for e in _sh_list_expiries(underlying, exchange=exchange)
        ]

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.shoonya_market_data import get_atm_chain as _sh_get_atm_chain

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
        from brokers.shoonya_market_data import (
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
        from brokers.shoonya_market_data import get_batch_quotes as _sh_get_batch_quotes

        return _sh_get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.shoonya_market_data import (
            get_batch_quotes_by_token as _sh_get_batch_quotes_by_token,
        )

        return _sh_get_batch_quotes_by_token(exchange, symbol_token_pairs, mode=mode)

    def get_spot_quote(self, underlying):
        from brokers.shoonya_market_data import get_spot_quote as _sh_get_spot_quote

        return _sh_get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # No broker-native FUTIDX resolution wired into this codebase for
        # Shoonya — always answers from the NSE/BSE public API, explicitly
        # flagged via FutSource so this is never a silent EQ/FUT split.
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.shoonya_market_data import (
            get_fno_underlyings as _sh_get_fno_underlyings,
        )

        return _sh_get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.shoonya_market_data import index_tokens as _sh_index_tokens

        return _sh_index_tokens()


class FallbackMarketData:
    """Wraps a primary MarketData provider with automatic failover to a
    secondary provider — used for MARKET_DATA_FALLBACK_PROVIDER (e.g.
    primary=SmartAPI, fallback=Shoonya when SmartAPI's feed is down).

    Circuit-breaker shaped, not retry-every-call: once the primary is
    considered down, it's marked down for `cooldown_s` and every wrapped
    method routes straight to the fallback until the cooldown expires —
    a single shared flag, not per-method, since a dead SmartAPI session
    fails the same way across list_expiries/get_atm_chain/etc. and
    there's no value in re-discovering that on every individual call.
    Without this, a fully down primary would pay its own failure latency
    (or ScripMaster reload / login-retry cost) on every tick before
    falling back.

    "Down" is judged differently depending on how the primary failed. A
    raised exception trips the breaker immediately — it's an unambiguous
    signal (auth failure, network error, dead session). An empty/falsy
    RESULT does not trip it immediately: that can just mean one symbol
    genuinely had no data this tick while the primary is otherwise fine,
    and tripping a shared 30s-wide breaker on a single blip needlessly
    blacks out every other wrapped method too — worse still if the
    fallback is unhealthy (e.g. Shoonya session dead), turning one
    harmless empty result into a real outage. Empty results only trip the
    breaker after `_EMPTY_TRIP_THRESHOLD` consecutive empties for that
    method; each individual empty call still answers from the fallback
    (so the caller isn't left hanging), it just doesn't lock out the
    primary while doing so.

    DELIBERATELY DOES NOT WRAP get_batch_quotes / get_batch_quotes_by_token.
    Those two are the ones ShoonyaMarketData's and UpstoxMarketData's own
    docstrings flag as a "KNOWN GAP": they return each broker's RAW quote
    row (Shoonya: lp/o/h/l/c/v/oi/nc/pc; Upstox: last_price/net_change/...)
    rather than a normalized shape, because nothing in this codebase's 7
    real call sites needed that translation layer built — until now,
    only ws_server_live.py's fetch_index_quotes_smartapi_sync() calls
    these, and it parses AngelOne's own field names directly. A silent
    failover on these two would hand that caller a differently-shaped
    row with no error — every field read as None, not an exception —
    which is worse than the primary just failing loudly. If a Shoonya (or
    Upstox) quote mapper for that call site gets built later, these two
    can move into the same failover path as everything else below.
    """

    # Methods safe to fail over: list_expiries/get_atm_chain/find_option_token/
    # get_spot_quote/get_fno_underlyings are all normalized to the Protocol's
    # documented shape at each adapter's own boundary (see UpstoxMarketData's
    # and ShoonyaMarketData's _to_iso/_to_shoonya-style conversions above).
    _FAILOVER_METHODS = (
        "list_expiries",
        "get_atm_chain",
        "find_option_token",
        "get_spot_quote",
        "get_futures_quote",
        "get_fno_underlyings",
    )

    # An exception is an unambiguous signal the primary itself is broken
    # (auth failure, network error, dead session) — trip the breaker on the
    # first one. An empty/falsy RESULT is not the same signal: it can also
    # mean "this one symbol genuinely has no data right now" (e.g. a spot
    # quote momentarily missing from a batch) while the primary is
    # otherwise completely healthy. Tripping the shared breaker on a
    # single empty result blacks out every failover-wrapped method for
    # `cooldown_s`, including ones that would have succeeded — and if the
    # fallback happens to be down too (e.g. Shoonya session dead), that
    # turns one harmless blip into a full outage. Require a few
    # consecutive empties (per method) before treating "empty" as "down".
    _EMPTY_TRIP_THRESHOLD = 3

    def __init__(self, primary, fallback, primary_name, fallback_name, cooldown_s=30):
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name
        self._cooldown_s = cooldown_s
        self._primary_down_until = 0.0  # 0 = not down; else a time.monotonic() deadline
        self._consecutive_empty: dict[str, int] = {}

    def _primary_is_down(self):
        return self._primary_down_until and time.monotonic() < self._primary_down_until

    def _mark_primary_down(self, method_name, reason):
        already_down = self._primary_is_down()
        self._primary_down_until = time.monotonic() + self._cooldown_s
        if not already_down:
            logger.warning(
                f"[market_data] {self._primary_name}.{method_name} {reason} — "
                f"routing to {self._fallback_name} for the next {self._cooldown_s}s"
            )

    def _call(self, method_name, *args, **kwargs):
        if not self._primary_is_down():
            try:
                result = getattr(self._primary, method_name)(*args, **kwargs)
            except Exception as exc:
                self._consecutive_empty[method_name] = 0
                self._mark_primary_down(method_name, f"raised ({exc})")
            else:
                if result:
                    self._consecutive_empty[method_name] = 0
                    return result
                streak = self._consecutive_empty.get(method_name, 0) + 1
                self._consecutive_empty[method_name] = streak
                if streak >= self._EMPTY_TRIP_THRESHOLD:
                    self._mark_primary_down(
                        method_name,
                        f"returned empty {streak}x in a row",
                    )
                else:
                    logger.info(
                        f"[market_data] {self._primary_name}.{method_name} returned "
                        f"empty ({streak}/{self._EMPTY_TRIP_THRESHOLD}) — retrying "
                        f"primary directly, not failing over yet"
                    )
                    return getattr(self._fallback, method_name)(*args, **kwargs)
        return getattr(self._fallback, method_name)(*args, **kwargs)

    def list_expiries(self, underlying, exchange="NFO"):
        return self._call("list_expiries", underlying, exchange=exchange)

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        return self._call(
            "get_atm_chain",
            underlying,
            expiry_ddmmmyyyy,
            strikes_around_atm,
            exchange=exchange,
        )

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        return self._call(
            "find_option_token",
            underlying,
            expiry_ddmmmyyyy,
            strike,
            opt_type,
            exchange=exchange,
        )

    def get_spot_quote(self, underlying):
        return self._call("get_spot_quote", underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        return self._call("get_futures_quote", underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        return self._call("get_fno_underlyings", force_refresh=force_refresh)

    # NOT routed through _call()/failover — see class docstring.
    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        return self._primary.get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        return self._primary.get_batch_quotes_by_token(
            exchange, symbol_token_pairs, mode=mode
        )

    def index_tokens(self):
        # Pure in-memory lookup dict, not a live call — always the primary's.
        return self._primary.index_tokens()


# ── NSE/BSE Public API provider ──────────────────────────────────────────
# The sixth market-data source: the PUBLIC, unauthenticated NSE/BSE REST
# endpoints behind market_api.py (option-chain v3, BSE JSON options,
# allIndices snapshot, public futures). No broker credentials, no login,
# no WebSocket — snapshot/polling only.
#
# Routing: the exchange is resolved from the requested underlying, never
# chosen by the caller. NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY (and any
# single-stock F&O) route to NSE; SENSEX/BANKEX/SENSEX50 route to BSE.
_BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}


def resolve_exchange_for_symbol(symbol: str) -> str:
    """Which exchange the NSE/BSE public API should query for `symbol`.

    Returns "BSE" for SENSEX/BANKEX/SENSEX50 (the three BSE-listed
    F&O indices this codebase supports) and "NSE" for everything else
    (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY and individual F&O stocks).

    This is the single routing decision both the NSE/BSE market-data
    adapter AND ws_server_live.py's runtime data-source switch use, so
    symbol → exchange can never drift between the two."""
    return "BSE" if symbol.strip().upper() in _BSE_SYMBOLS else "NSE"


def _normalize_expiry_dash(expiry) -> Optional[str]:
    """'31JUL2026' | '31-Jul-2026' | '2026-07-31' -> '31-Jul-2026'
    (market_api.py's native option-chain expiry format)."""
    if not expiry:
        return expiry
    for fmt in ("%d-%b-%Y", "%d%b%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(expiry), fmt).strftime("%d-%b-%Y")
        except ValueError:
            continue
    raise ValueError(f"Unsupported expiry format: {expiry!r}")


def _atm_from_rows(rows, spot) -> float:
    if not rows or not spot:
        return 0.0
    strikes = sorted({float(r.get("strike")) for r in rows if r.get("strike") is not None})
    if not strikes:
        return 0.0
    return float(min(strikes, key=lambda s: abs(s - spot)))


class NseBseMarketData:
    """Market-data provider backed by the PUBLIC NSE/BSE REST endpoints in
    market_api.py — zero broker credentials required.

    Exchange routing is symbol-driven (see resolve_exchange_for_symbol):
    NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY -> NSE option-chain-v3 + allIndices;
    SENSEX/BANKEX/SENSEX50 -> BSE JSON options + BSE index quote.

    Snapshot/polling only: there is no WebSocket client for the public
    NSE/BSE APIs, so PROVIDER_CAPABILITIES marks this provider
    snapshot=True / websocket=False / execution=False and the dashboard
    labels its status POLLING — the expected mode for this provider, not
    an error condition.

    NEVER an execution broker: NSE/BSE is a read-only market-data source.
    config.py rejects EXCHANGE_BROKER=NSE_BSE at startup so it can't be
    wired into order routing.

    Failures RAISE (rather than returning None) so that when this provider
    is wrapped by FallbackMarketData — either as primary or as
    MARKET_DATA_FALLBACK_PROVIDER — the circuit breaker can route to the
    other provider instead of the caller silently receiving partial data.
    """

    def list_expiries(self, underlying, exchange="NFO"):
        underlying = underlying.upper()
        if underlying in _BSE_SYMBOLS:
            # BSE expiry calendar is computed (no public BSE expiry-list
            # endpoint) — same helper the BSE chain path already uses.
            from expiry_manager import _generate_bse_expiry_series

            return _generate_bse_expiry_series(underlying)
        # NSE: public daily ScripMaster (cached, no broker login needed).
        from broker_pipeline import get_available_expiries

        return get_available_expiries(underlying, exchange=exchange)

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        del strikes_around_atm  # public API returns the whole expiry; caller filters
        from market_api import (
            BSE_INDEX_SCRIP_CODES,
            fetch_bse_json_options,
            fetch_option_chain,
            parse_option_chain_response,
        )

        underlying = underlying.upper()
        expiry_dash = _normalize_expiry_dash(expiry_ddmmmyyyy)
        if underlying in _BSE_SYMBOLS:
            scrip_cd = BSE_INDEX_SCRIP_CODES.get(underlying)
            if not scrip_cd:
                raise RuntimeError(f"No public BSE derivative code for {underlying}")
            df, spot = fetch_bse_json_options(
                expiry_dash.replace("-", " "), scrip_cd=scrip_cd
            )
            if df is None or df.empty:
                raise RuntimeError(
                    f"Public BSE option chain empty for {underlying} {expiry_dash}"
                )
            df = df.rename(columns={"Strike": "StrikePrice"})
            spot = float(df["Spot"].iloc[0]) if "Spot" in df.columns else 0.0
        else:
            payload = fetch_option_chain(underlying, expiry_dash)
            df = parse_option_chain_response(payload, expiry_dash)
            spot = float(df["Spot"].iloc[0]) if "Spot" in df.columns else 0.0

        if df.empty:
            raise RuntimeError(f"Public {underlying} chain empty for {expiry_dash}")

        rows = []
        for rec in df.to_dict("records"):
            strike = rec.get("StrikePrice")
            if strike is None:
                continue
            for side in ("CE", "PE"):
                rows.append(
                    {
                        "strike": float(strike),
                        "type": side,
                        "tradingsymbol": None,
                        "token": None,
                        "ltp": _safe_float(rec.get(f"{side}_LTP")),
                        "open": None,
                        "high": None,
                        "low": None,
                        "close": None,
                        "oi": _safe_float(rec.get(f"{side}_OI")),
                        "volume": _safe_float(rec.get(f"{side}_Volume")),
                        "net_change": rec.get(f"{side}_Change"),
                        "pct_change": rec.get(f"{side}_pChange"),
                    }
                )
        rows.sort(key=lambda r: (r["strike"], r["type"]))
        return {
            "underlying": underlying,
            "spot": spot,
            "atm_strike": _atm_from_rows(rows, spot),
            "expiry": expiry_ddmmmyyyy,
            "rows": rows,
        }

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        # The public APIs have no token/tradingsymbol concept — callers that
        # need one must use a broker provider. Returns None (unresolved).
        return None

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        # No batch-quote endpoint on the public APIs; the pipeline reads the
        # full chain via get_atm_chain()/option_chain_json instead.
        return {}

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        return {}

    def get_spot_quote(self, underlying):
        from market_api import (
            INDEX_RENAME,
            fetch_all_indices_snapshot,
            fetch_bse_index_quote,
            get_index_from_snapshot,
        )

        underlying = underlying.upper()
        if underlying in _BSE_SYMBOLS:
            entry = fetch_bse_index_quote(underlying)
            if not entry:
                return None
            return {
                "ltp": _safe_float(entry.get("Last Price")),
                "open": None,
                "high": None,
                "low": None,
                "close": _safe_float(entry.get("Prev Close")),
            }
        snapshot = fetch_all_indices_snapshot()
        if snapshot.empty:
            raise RuntimeError(f"NSE allIndices snapshot returned no data")
        inverse = {v: k for k, v in INDEX_RENAME.items()}
        display = inverse.get(underlying, underlying)
        row = get_index_from_snapshot(snapshot, display) or get_index_from_snapshot(
            snapshot, underlying
        )
        if not row:
            raise RuntimeError(f"No allIndices row for {underlying}")
        return {
            "ltp": _safe_float(row.get("last")),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("previousClose")),
        }

    def get_futures_quote(self, underlying, which="NEAR"):
        # This provider IS the NSE/BSE public API, so FutSource="NSE_BSE"
        # here reflects the actual source rather than flagging a fallback.
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        # Public ScripMaster universe — no broker login required.
        from brokers.smartapi_instruments import (
            get_fno_underlyings as _public_fno_underlyings,
        )

        return _public_fno_underlyings(refresh=force_refresh)

    def index_tokens(self):
        # No token model on the public APIs — index quotes go through
        # get_spot_quote()/fetch_all_indices_snapshot() instead.
        return {}


# ── Kite Connect provider ────────────────────────────────────────────────
class KiteMarketData:
    """Adapter over brokers.kite_client, implementing the same MarketData
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
        from brokers.kite_client import list_expiries as _k_list_expiries

        return _k_list_expiries(underlying, exchange=exchange)

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.kite_client import get_atm_chain as _k_get_atm_chain

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
        from brokers.kite_client import find_option_token as _k_find_option_token

        return _k_find_option_token(
            underlying, expiry_ddmmmyyyy, strike, opt_type, exchange=exchange
        )

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        del mode
        from brokers.kite_client import get_quotes as _k_get_quotes

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
        from brokers.kite_client import get_spot_quote as _k_get_spot_quote

        return _k_get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # No broker-native FUTIDX resolution wired into this codebase for
        # Kite — always answers from the NSE/BSE public API, explicitly
        # flagged via FutSource so this is never a silent EQ/FUT split.
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        # Public ScripMaster universe (same source kite_client's instrument
        # dump comes from, minus the broker session requirement).
        from brokers.smartapi_instruments import (
            get_fno_underlyings as _public_fno_underlyings,
        )

        return _public_fno_underlyings(refresh=force_refresh)

    def index_tokens(self):
        # Kite has no index token model — index quotes go through
        # get_spot_quote() (see fetch_index_quotes_smartapi_sync()'s
        # provider-aware branch for KITE/BREEZE).
        return {}


# ── ICICI Breeze provider ────────────────────────────────────────────────
class BreezeMarketData:
    """Adapter over brokers.breeze_market_data's module-level functions,
    implementing the same MarketData Protocol. NOTE: this is a DIFFERENT
    class from brokers.breeze_market_data's own BreezeMarketData — that one
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
        (brokers.breeze_market_data's native expiry convention)."""
        if not expiry:
            return expiry
        for fmt in ("%d-%b-%Y", "%d%b%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(expiry), fmt).strftime("%d-%b-%Y")
            except ValueError:
                continue
        raise ValueError(f"Unsupported expiry format: {expiry!r}")

    @staticmethod
    def _to_ddmmmyyyy(expiry_dash):
        """'31-Jul-2026' -> '31JUL2026' (upper-cased, matching the
        Protocol's SmartAPI convention)."""
        if not expiry_dash:
            return expiry_dash
        return (
            datetime.strptime(str(expiry_dash), "%d-%b-%Y")
            .strftime("%d%b%Y")
            .upper()
        )

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.breeze_market_data import list_expiries as _bz_list_expiries

        return [
            self._to_ddmmmyyyy(e)
            for e in _bz_list_expiries(underlying, exchange=exchange)
        ]

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.breeze_market_data import get_atm_chain as _bz_get_atm_chain

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
        from brokers.breeze_market_data import (
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
        from brokers.breeze_market_data import (
            get_batch_quotes as _bz_get_batch_quotes,
        )

        return _bz_get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.breeze_market_data import (
            get_batch_quotes_by_token as _bz_get_batch_quotes_by_token,
        )

        return _bz_get_batch_quotes_by_token(
            exchange, symbol_token_pairs, mode=mode
        )

    def get_spot_quote(self, underlying):
        from brokers.breeze_market_data import get_spot_quote as _bz_get_spot_quote

        return _bz_get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # No broker-native FUTIDX resolution wired into this codebase for
        # Breeze — always answers from the NSE/BSE public API, explicitly
        # flagged via FutSource so this is never a silent EQ/FUT split.
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.breeze_market_data import (
            get_fno_underlyings as _bz_get_fno_underlyings,
        )

        return _bz_get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.breeze_market_data import index_tokens as _bz_index_tokens

        return _bz_index_tokens()


class KotakMarketData:
    """Adapter over brokers.kotak_market_data's module-level functions,
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
        (brokers.kotak_market_data's native expiry convention)."""
        if not expiry:
            return expiry
        for fmt in ("%d-%b-%Y", "%d%b%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(expiry), fmt).strftime("%d-%b-%Y")
            except ValueError:
                continue
        raise ValueError(f"Unsupported expiry format: {expiry!r}")

    @staticmethod
    def _to_ddmmmyyyy(expiry_dash):
        """'31-Jul-2026' -> '31JUL2026' (upper-cased, matching the
        Protocol's SmartAPI convention)."""
        if not expiry_dash:
            return expiry_dash
        return (
            datetime.strptime(str(expiry_dash), "%d-%b-%Y")
            .strftime("%d%b%Y")
            .upper()
        )

    def list_expiries(self, underlying, exchange="NFO"):
        from brokers.kotak_market_data import list_expiries as _kk_list_expiries

        return [
            self._to_ddmmmyyyy(e)
            for e in _kk_list_expiries(underlying, exchange=exchange)
        ]

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        from brokers.kotak_market_data import get_atm_chain as _kk_get_atm_chain

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
        from brokers.kotak_market_data import (
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
        from brokers.kotak_market_data import (
            get_batch_quotes as _kk_get_batch_quotes,
        )

        return _kk_get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        from brokers.kotak_market_data import (
            get_batch_quotes_by_token as _kk_get_batch_quotes_by_token,
        )

        return _kk_get_batch_quotes_by_token(
            exchange, symbol_token_pairs, mode=mode
        )

    def get_spot_quote(self, underlying):
        from brokers.kotak_market_data import get_spot_quote as _kk_get_spot_quote

        return _kk_get_spot_quote(underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        # Kotak's own SDK resolves and quotes FUTIDX/FUTSTK natively (this
        # is the one non-SmartAPI provider that does) — especially
        # important for SENSEX/BANKEX, where the public BSE futures table
        # can have rows but omit LTP. Falls back to the NSE/BSE public API,
        # explicitly flagged via FutSource, only when Kotak's own
        # resolution comes back empty — never silently.
        from brokers.kotak_market_data import get_futures_quote as _kk_get_futures_quote

        quote = _kk_get_futures_quote(underlying, which=which)
        if quote:
            quote = dict(quote)
            quote["FutSource"] = "KOTAK"
            return quote
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        from brokers.kotak_market_data import (
            get_fno_underlyings as _kk_get_fno_underlyings,
        )

        return _kk_get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        from brokers.kotak_market_data import index_tokens as _kk_index_tokens

        return _kk_index_tokens()


def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── Provider registry + runtime switching ────────────────────────────────
# Seven selectable market-data sources (see PROVIDER_CAPABILITIES). The
# `market_data` singleton below is a stable facade that delegates to the
# CURRENTLY ACTIVE provider, so set_active_provider() can switch sources at
# runtime without any of the by-name imports elsewhere
# (broker_pipeline.py, mTerminals_json.py) needing to be
# re-executed — the one architectural requirement for the Dashboard's
# DATA SOURCE dropdown to work without a server restart.
_PROVIDERS = {
    "SMARTAPI": SmartApiMarketData,
    "UPSTOX": UpstoxMarketData,
    "SHOONYA": ShoonyaMarketData,
    "KITE": KiteMarketData,
    "BREEZE": BreezeMarketData,
    "KOTAK": KotakMarketData,
    "NSE_BSE": NseBseMarketData,
}

# Provider -> capability flags. `websocket`/`execution` reflect what THIS
# codebase actually implements (verified against backend/brokers/): Kite,
# Breeze and Kotak have REST market-data clients but no WebSocket tick
# client, and NSE/BSE public API has neither. `execution: False` marks the
# source as read-only — never usable as EXECUTION_BROKER (config.py
# enforces this). Kotak's capability flags: its SDK supports order routing
# (place_order) so execution: True is technically true, but no Kotak
# execution adapter is wired into this codebase yet — keep execution: False
# until one exists so a user can't select an execution broker that has no
# order path (fail safe, same reasoning as the NSE_BSE read-only guard).
PROVIDER_CAPABILITIES: dict[str, dict] = provider_capabilities()

# Dashboard display names — one logical "NSE/BSE API" option; the backend
# resolves the NSE vs BSE adapter from the selected symbol.
PROVIDER_DISPLAY_NAMES: dict[str, str] = provider_display_names()


def provider_has_credentials(name: str) -> bool:
    """Whether the given provider has usable credentials configured.
    NSE/BSE public API needs none (it is the login-free fallback)."""
    name = normalize_provider(name)
    s = _md_settings
    if name == "NSE_BSE":
        return True
    if name == "SMARTAPI":
        return bool(s.smartapi_key and s.smartapi_client_code)
    if name == "UPSTOX":
        return bool(s.upstox_access_token)
    if name == "KITE":
        return bool(s.kite_access_token)
    if name == "SHOONYA":
        return bool(s.shoonya_user_id and s.shoonya_password and s.shoonya_totp_secret)
    if name == "BREEZE":
        return bool(s.breeze_api_key and s.breeze_api_secret and s.breeze_api_session)
    if name == "KOTAK":
        return bool(
            s.kotak_consumer_key
            and s.kotak_mobile
            and s.kotak_ucc
            and s.kotak_totp_secret
            and s.kotak_mpin
        )
    return False


_primary_name = (
    _md_settings.market_data_provider
    if _md_settings.market_data_provider in _PROVIDERS
    else "SMARTAPI"
)
_primary_instance = _PROVIDERS[_primary_name]()

_fallback_name = _md_settings.market_data_fallback_provider


def _build_instance(name: str):
    """Build a fresh provider instance for `name`, optionally wrapped for
    failover by MARKET_DATA_FALLBACK_PROVIDER (re-resolved from settings
    each time, so a switch also re-evaluates the fallback wrapper)."""
    primary = _PROVIDERS[name]()
    if _fallback_name and _fallback_name in _PROVIDERS and _fallback_name != name:
        return FallbackMarketData(
            primary,
            _PROVIDERS[_fallback_name](),
            primary_name=name,
            fallback_name=_fallback_name,
        )
    return primary


# The currently active provider. Initialized from config at import time but
# switchable at runtime via set_active_provider() (ws_server_live.py's
# ?dataSource= handler). Kept in module-level state — not on the facade —
# so get_active_provider()/provider_status() stay cheap and synchronous.
_active_provider_name = _primary_name
_active_provider_instance = _build_instance(_primary_name)


class _SwitchingMarketData:
    """Stable facade over the active provider instance. `market_data` binds
    to THIS object at import time in broker_pipeline.py and
    mTerminals_json.py; __getattr__ delegates every MarketData method to
    whichever provider is active, so set_active_provider() swaps the source
    everywhere without any module re-import."""

    def __getattr__(self, name):
        return getattr(_active_provider_instance, name)

    def __repr__(self):
        return f"<SwitchingMarketData active={_active_provider_name!r}>"


market_data: MarketData = _SwitchingMarketData()


def get_active_provider() -> str:
    """Key of the currently active market-data provider (e.g. "NSE_BSE",
    "UPSTOX"). Runtime-switchable — the single source of truth the option
    chain pipeline and index-quote loops route on, replacing the frozen
    settings.market_data_provider reads."""
    return _active_provider_name


def set_active_provider(name: str) -> bool:
    """Runtime provider switch.

    Returns True when the requested provider becomes active.
    Returns False when the provider is temporarily unavailable.

    Raises ValueError for an unknown provider key.
    """
    global _active_provider_name, _active_provider_instance

    name = name.strip().upper()

    if name not in _PROVIDERS:
        raise ValueError(
            f"Unknown market-data provider {name!r}. Valid: {sorted(_PROVIDERS)}"
        )

    if name == _active_provider_name:
        return True

    # Broker authentication is preflighted through one common connection
    # boundary. It keeps this provider registry independent of each SDK's
    # login implementation and commits the switch only after readiness is
    # known (Shoonya currently performs the only such check).
    from brokers.connection import check_connection
    connection = check_connection(name)
    if not connection.ready:
        broker_event(
            logger,
            provider=name,
            operation="provider_switch",
            status="rejected",
            level=logging.WARNING,
            reason=connection.error,
        )
        logger.warning(
            "[market_data] %s unavailable; switch rejected; "
            "keeping active provider %s: %s",
            name,
            _active_provider_name,
            connection.error,
        )
        return False

    # Build the candidate before changing global state.
    candidate = _build_instance(name)

    _active_provider_name = name
    _active_provider_instance = candidate

    broker_event(
        logger,
        provider=name,
        operation="provider_switch",
        status="active",
    )
    logger.info("[market_data] active provider switched to %s", name)
    return True


def provider_status() -> list[dict]:
    """Per-provider UI status for the Dashboard's DATA SOURCE picker.

    Each entry: {"id", "label", "status", "active", "capabilities"} where
    status is one of:
      LIVE              — broker provider with creds AND a WebSocket feed
                          client in this codebase (SMARTAPI/UPSTOX/SHOONYA)
      POLLING           — snapshot/REST-only delivery (NSE/BSE always;
                          KITE/BREEZE have no WS client). Expected mode, NOT
                          an error — the dashboard must render it neutrally.
      UNAVAILABLE       — broker provider with missing/expired credentials
      SESSION_REQUIRED  — BREEZE's daily session token is not populated (it
                          has no automated refresh; see .env)
    """
    out = []
    for key in PROVIDER_KEYS:
        caps = PROVIDER_CAPABILITIES[key]
        if key == "NSE_BSE":
            status = "POLLING"
        elif key == "BREEZE" and not provider_has_credentials(key):
            status = "SESSION_REQUIRED"
        elif not provider_has_credentials(key):
            status = "UNAVAILABLE"
        elif caps.get("websocket"):
            status = "LIVE"
        else:
            status = "POLLING"
        out.append(
            {
                "id": key,
                "label": PROVIDER_DISPLAY_NAMES[key],
                "status": status,
                "active": key == _active_provider_name,
                "capabilities": dict(caps),
            }
        )
    return out