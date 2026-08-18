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

import logging
import time
from datetime import datetime
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

from config import settings as _md_settings


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
        return {
            "ltp": quote.get("last_price"),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
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


# Selected by config.settings.market_data_provider (SMARTAPI default) and
# optionally wrapped for failover by market_data_fallback_provider. See
# UpstoxMarketData's and ShoonyaMarketData's docstrings for the one known
# gap each before relying on either as a PRIMARY in production; see
# FallbackMarketData's docstring above for the same caveat as a fallback.
_PROVIDERS = {
    "SMARTAPI": SmartApiMarketData,
    "UPSTOX": UpstoxMarketData,
    "SHOONYA": ShoonyaMarketData,
}

_primary_name = (
    _md_settings.market_data_provider
    if _md_settings.market_data_provider in _PROVIDERS
    else "SMARTAPI"
)
_primary_instance = _PROVIDERS[_primary_name]()

_fallback_name = _md_settings.market_data_fallback_provider
if _fallback_name and _fallback_name in _PROVIDERS and _fallback_name != _primary_name:
    market_data: MarketData = FallbackMarketData(
        _primary_instance,
        _PROVIDERS[_fallback_name](),
        primary_name=_primary_name,
        fallback_name=_fallback_name,
    )
else:
    market_data: MarketData = _primary_instance
