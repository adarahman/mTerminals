"""Broker-neutral symbol and market-provider metadata for the dashboard."""

import logging

logger = logging.getLogger(__name__)

FNO_SYMBOLS_FALLBACK = {
    "indices": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"],
    "stocks": [],
}
_SYMBOL_DISPLAY_NAMES = {}


def _configured_market_data():
    try:
        from infrastructure.config import settings

        if not settings.broker_services_enabled:
            return None
    except ImportError:
        pass
    try:
        from brokers.market_data_registry import market_data

        return market_data
    except ImportError:
        return None


_FNO_SYMBOLS_CACHE = None


def get_fno_symbols():
    global _FNO_SYMBOLS_CACHE
    if _FNO_SYMBOLS_CACHE is not None:
        return _FNO_SYMBOLS_CACHE
    result = FNO_SYMBOLS_FALLBACK
    try:
        from brokers.smartapi.instruments import get_fno_underlyings

        symbols = get_fno_underlyings()
        if symbols.get("indices") or symbols.get("stocks"):
            result = symbols
    except Exception as error:
        logger.warning("Public ScripMaster universe lookup failed: %s", error)

    if result is FNO_SYMBOLS_FALLBACK:
        market_data = _configured_market_data()
        if market_data is not None:
            try:
                symbols = market_data.get_fno_underlyings()
                if symbols.get("indices") or symbols.get("stocks"):
                    result = symbols
            except Exception as error:
                logger.warning("Provider F&O universe lookup failed: %s", error)
    _FNO_SYMBOLS_CACHE = result
    return _FNO_SYMBOLS_CACHE


def get_symbol_display_name(symbol):
    key = str(symbol or "").strip().upper()
    if not key:
        return ""
    if key in _SYMBOL_DISPLAY_NAMES:
        return _SYMBOL_DISPLAY_NAMES[key]
    try:
        from brokers.upstox.client import get_company_name_for_ticker

        name = get_company_name_for_ticker(key)
    except Exception:
        name = None
    if not name:
        try:
            from brokers.symbol_names import _COMMON_UNDERLYING_ALIASES

            name = next(
                (label for label, ticker in _COMMON_UNDERLYING_ALIASES.items() if ticker == key),
                None,
            )
        except Exception:
            name = None
    if isinstance(name, str) and name.isupper():
        name = name.title()
    _SYMBOL_DISPLAY_NAMES[key] = name or key
    return _SYMBOL_DISPLAY_NAMES[key]


def active_data_source():
    try:
        from brokers.market_data_registry import get_active_provider

        return get_active_provider()
    except Exception:
        return "SMARTAPI"


def data_sources_payload():
    try:
        from brokers.market_data_registry import provider_status

        return provider_status()
    except Exception:
        return []
