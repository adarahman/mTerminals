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
import time

from application.index_quote_cache import (
    IndexQuoteCache,
    TICKER_SYMBOLS as _TICKER_SYMBOLS,
    NSE_TICKER_SYMBOLS as _NSE_TICKER_SYMBOLS,
    BSE_TICKER_SYMBOLS as _BSE_TICKER_SYMBOLS,
    VIX_CACHE_KEY as _VIX_TRADINGSYMBOL,
    VIX_TOKEN as _VIX_TOKEN,
)

from brokers.market_data_registry import (
    market_data,
    get_active_provider,
)

from brokers.smartapi.client import safe_float
from application.market_pipeline.utils import safe_float
from market.quotes.ticker_payload import build_ticker_payload
from market.quotes.vix_service import resolve_vix

from storage.caches import _BATCH_CACHE


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Warning throttling
# ---------------------------------------------------------------------

_WARN_COOLDOWNS: dict[str, float] = {}


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

    index_tokens = market_data.index_tokens()

    symbols = {}

    for sym in _TICKER_SYMBOLS:
        token = index_tokens.get(sym)

        if token:
            symbols[sym] = token


    for sym in _NSE_TICKER_SYMBOLS:
        token = index_tokens.get(sym)

        if token:
            symbols[sym] = token


    for sym in _BSE_TICKER_SYMBOLS:
        token = index_tokens.get(sym)

        if token:
            symbols[sym] = token


    if _VIX_TOKEN:
        symbols[_VIX_TRADINGSYMBOL] = _VIX_TOKEN


    if not symbols:
        return {}

    symbol_token_pairs = list(symbols.items())

    quotes = market_data.get_batch_quotes_by_token(
        "NSE",
        symbol_token_pairs,
    )

    _BATCH_CACHE.refill(quotes)

    return quotes


def fetch_vix_smartapi():
    """
    Temporary safe VIX fetch.
    VIX failure must not stop option chain pipeline.
    """
    try:
        broker_quote = _BATCH_CACHE.get(_VIX_TRADINGSYMBOL)

        return resolve_vix(
            broker_quote,
            public_loader=lambda: (None, 0.0, None),
            safe_number=safe_float,
            warn=lambda key, msg: logger.warning("[%s] %s", key, msg),
        )

    except Exception as exc:
        logger.warning("[vix] disabled temporarily: %s", exc)
        return None, 0.0


def fetch_ticker_payload_smartapi(
    symbols=None,
):
    if not symbols:
        symbols = list(symbols or [])

    return build_ticker_payload(
        symbols or [],
        quote_lookup=_BATCH_CACHE.get,
        safe_number=safe_float,
    )

def fetch_sensex_ticker_smartapi():
    """
    Fetch SENSEX ticker data.
    """

    return fetch_ticker_payload_smartapi(
        ["SENSEX"]
    )


# ---------------------------------------------------------------------
# Compatibility aliases
# Temporary until run_server.py removal
# ---------------------------------------------------------------------

fetch_vix = fetch_vix_smartapi

fetch_ticker_payload = fetch_ticker_payload_smartapi

fetch_sensex_ticker = fetch_sensex_ticker_smartapi