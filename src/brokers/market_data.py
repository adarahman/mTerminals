"""Compatibility façade for the canonical market-data provider modules.

New architecture code imports providers from their owning broker package and
runtime selection from :mod:`brokers.market_data_registry`.
"""
from brokers.base import MarketDataProvider as MarketData
from brokers.breeze.adapter import BreezeMarketData
from brokers.kite.market_data import KiteMarketData
from brokers.kotak.adapter import KotakMarketData
from brokers.shoonya.adapter import ShoonyaMarketData
from brokers.smartapi.market_data import SmartApiMarketData
from brokers.upstox.market_data import UpstoxMarketData
from market.providers.fallback import FallbackMarketData
from market.providers.nse_bse import (
    NseBseMarketData,
    _atm_from_rows,
    _normalize_expiry_dash,
    resolve_exchange_for_symbol,
)
from brokers.provider_registry import (
    PROVIDER_KEYS,
    provider_capabilities,
    provider_display_names,
)

PROVIDER_CAPABILITIES = provider_capabilities()
PROVIDER_DISPLAY_NAMES = provider_display_names()


class _RegistryMarketDataProxy:
    def __getattr__(self, name):
        from brokers.market_data_registry import market_data as active_market_data

        return getattr(active_market_data, name)

    def __repr__(self):
        from brokers.market_data_registry import market_data as active_market_data

        return repr(active_market_data)


market_data = _RegistryMarketDataProxy()


def get_active_provider():
    from brokers.market_data_registry import get_active_provider as get_provider

    return get_provider()


def set_active_provider(name):
    from brokers.market_data_registry import set_active_provider as set_provider

    return set_provider(name)


def provider_has_credentials(name):
    from brokers.market_data_registry import provider_has_credentials as has_credentials

    return has_credentials(name)


def provider_status():
    from brokers.market_data_registry import provider_status as get_status

    return get_status()
def get_atm_chain(
    underlying,
    expiry,
    strikes_around_atm=10,
    exchange="NFO",
):
    return market_data.get_atm_chain(
        underlying,
        expiry,
        strikes_around_atm=strikes_around_atm,
        exchange=exchange,
    )

__all__ = [
    "MarketData",
    "SmartApiMarketData",
    "UpstoxMarketData",
    "ShoonyaMarketData",
    "KiteMarketData",
    "BreezeMarketData",
    "KotakMarketData",
    "NseBseMarketData",
    "FallbackMarketData",
    "PROVIDER_KEYS",
    "PROVIDER_CAPABILITIES",
    "PROVIDER_DISPLAY_NAMES",
    "market_data",
    "get_active_provider",
    "set_active_provider",
    "provider_has_credentials",
    "provider_status",
    "resolve_exchange_for_symbol",
    "get_atm_chain",
]
