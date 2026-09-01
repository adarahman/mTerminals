"""
Application market pipeline — quote assembly.

Responsibilities:
- batch index quotes
- VIX resolution
- ticker payload creation
- SENSEX ticker handling

No broker-specific authentication/session logic here.
Provider routing comes from brokers.market_data_registry.
"""

from __future__ import annotations

import logging
import threading
import time

from application.index_quote_cache import (
    IndexQuoteCache,
    TICKER_SYMBOLS as _TICKER_SYMBOLS,
    VIX_CACHE_KEY as _VIX_TRADINGSYMBOL,
    VIX_TOKEN as _VIX_TOKEN,
)

from brokers.market_data_registry import (
    market_data,
    get_active_provider,
)

from application.market_pipeline.utils import safe_float
from market.quotes.ticker_payload import build_ticker_payload
from market.quotes.vix_service import resolve_vix

from storage.caches import _BATCH_CACHE
from market.providers.nse_bse import NseBseMarketData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Warning throttling
# ---------------------------------------------------------------------

_WARN_COOLDOWNS: dict[str, float] = {}
_BATCH_WARM_LOCK = threading.Lock()


def _throttled_warning(
    key: str,
    msg: str,
    cooldown_s: float = 60.0,
) -> None:
    """
    Emit repeated provider warnings only periodically.

    Prevents dead providers from flooding logs every polling cycle.
    """
    now = time.monotonic()

    last = _WARN_COOLDOWNS.get(key)

    if last is not None and (now - last) < cooldown_s:
        return

    _WARN_COOLDOWNS[key] = now

    logger.warning(msg)

_PUBLIC_MARKET_DATA = NseBseMarketData()


def _load_public_vix():
    """Canonical INDIA VIX fallback independent of selected broker."""
    try:
        quote = _PUBLIC_MARKET_DATA.get_spot_quote("INDIA VIX")
        if not quote:
            return None, 0.0, None

        vix = safe_float(
            quote.get("ltp")
            or quote.get("last_price")
            or quote.get("lastPrice")
        )

        change_pct = safe_float(
            quote.get("pChange")
            or quote.get("pct_change")
            or quote.get("change_percent")
            or 0.0
        )

        return vix, change_pct, quote

    except Exception as exc:
        _throttled_warning(
            "public-vix",
            f"[vix:public] NSE fallback failed: {exc}",
        )
        return None, 0.0, None
# ---------------------------------------------------------------------
# Shared index quote cache
# ---------------------------------------------------------------------

_INDEX_QUOTES = IndexQuoteCache(
    market_data,
    active_provider=get_active_provider,
    safe_number=safe_float,
    warn=_throttled_warning,
    logger=logger,
)


# ---------------------------------------------------------------------
# Public quote functions
# ---------------------------------------------------------------------

def fetch_all_pills_and_vix_batched():
    """
    Fetch dashboard index pills and India VIX.

    Populates:
        storage.caches._BATCH_CACHE

    Symbols:
        NSE indices
        BSE indices
        India VIX

    Called once per market-data cycle.
    """

    # A timed-out SDK request keeps running because Python cannot stop a
    # worker thread. Do not queue another identical REST request on the next
    # poll; consumers continue reading the last successful cache snapshot.
    if not _BATCH_WARM_LOCK.acquire(blocking=False):
        return {}

    try:
        return _fetch_all_pills_and_vix_batched_locked()
    finally:
        _BATCH_WARM_LOCK.release()


def _fetch_all_pills_and_vix_batched_locked():
    index_tokens = market_data.index_tokens()
    pairs_by_exchange = _index_pairs_by_exchange(
        index_tokens,
        include_vix=get_active_provider() == "SMARTAPI",
    )
    if not pairs_by_exchange:
        return {}

    provider_options = (
        {
            "rate_limit_max_retries": 1,
            "rate_limit_backoff_s": 0.25,
        }
        if get_active_provider() == "SMARTAPI"
        else {}
    )
    quotes = {}
    for exchange, symbol_token_pairs in pairs_by_exchange.items():
        by_token = market_data.get_batch_quotes_by_token(
            exchange,
            symbol_token_pairs,
            **provider_options,
        )
        quotes.update(_quotes_by_symbol(symbol_token_pairs, by_token))

    # An empty/failed response must not erase the last known-good snapshot.
    if quotes:
        _BATCH_CACHE.refill(quotes)

    return quotes


def _index_pairs_by_exchange(index_tokens, *, include_vix=False):
    pairs_by_exchange = {}
    for symbol in _TICKER_SYMBOLS:
        info = index_tokens.get(symbol)
        if not isinstance(info, dict) or not info.get("token"):
            continue
        exchange = info.get("exchange", "NSE")
        pairs_by_exchange.setdefault(exchange, []).append(
            (symbol, str(info["token"]))
        )
    if include_vix and _VIX_TOKEN:
        pairs_by_exchange.setdefault("NSE", []).append(
            (_VIX_TRADINGSYMBOL, str(_VIX_TOKEN))
        )
    return pairs_by_exchange


def _quotes_by_symbol(symbol_token_pairs, quotes_by_token):
    return {
        symbol: quotes_by_token[str(token)]
        for symbol, token in symbol_token_pairs
        if str(token) in quotes_by_token
    }


def fetch_vix_smartapi():
    """
    Resolve INDIA VIX independently of the selected market-data provider.

    Preference:
      1. active broker batch quote
      2. public NSE/BSE provider
    """
    try:
        broker_quote = _BATCH_CACHE.get(_VIX_TRADINGSYMBOL)

        return resolve_vix(
            broker_quote,
            public_loader=_load_public_vix,
            safe_number=safe_float,
            warn=lambda key, msg: _throttled_warning(
                key,
                f"[{key}] {msg}",
            ),
        )

    except Exception as exc:
        _throttled_warning(
            "vix-resolve",
            f"[vix] resolution failed: {exc}",
        )
        return None, 0.0


def fetch_ticker_payload_smartapi(
    symbols=None,
):
    if symbols is None:
        symbols = list(_TICKER_SYMBOLS)

    return build_ticker_payload(
        symbols or [],
        quote_lookup=_BATCH_CACHE.get,
        safe_number=safe_float,
    )

def fetch_sensex_ticker_smartapi():
    """
    Fetch SENSEX ticker data.
    """

    payload = fetch_ticker_payload_smartapi(["SENSEX"])
    return payload[0] if payload else None


# ---------------------------------------------------------------------
# Compatibility aliases
# Temporary until src/server/app.py composition root is split into server/* modules
# ---------------------------------------------------------------------

fetch_vix = fetch_vix_smartapi

fetch_ticker_payload = fetch_ticker_payload_smartapi

fetch_sensex_ticker = fetch_sensex_ticker_smartapi
