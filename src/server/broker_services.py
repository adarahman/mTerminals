"""Broker SDK surface, gated by BROKER_SERVICES_ENABLED.

Importing broker modules initializes SDKs and the instrument master, so in
public-only mode they are never imported and every exported callable fails
closed instead of silently logging in. The broker adapter registry is still
imported lazily by the option-chain pipeline; the stubs here let server
composition and the DATA SOURCE
dropdown/reporting work in public-only mode too.

Export names form the stable broker-service boundary consumed by the server.
"""
from infrastructure.config import settings as broker_settings

BROKER_SERVICES_ENABLED = broker_settings.broker_services_enabled


def _disabled(*_args, **_kwargs):
    raise RuntimeError("Broker services are disabled by configuration")


if BROKER_SERVICES_ENABLED:
    from brokers.market_data_registry import (  # noqa: F401
        market_data,
        PROVIDER_CAPABILITIES as MD_PROVIDER_CAPABILITIES,
        PROVIDER_KEYS as MD_PROVIDER_KEYS,
        get_active_provider as md_get_active_provider,
        provider_has_credentials as md_provider_has_credentials,
        provider_status as md_provider_status,
        set_active_provider as md_set_active_provider,
    )
    from brokers.connection import get_execution_adapter

    _execution_adapter = get_execution_adapter(broker_settings.execution_broker)
    smartapi_place_order = _execution_adapter.place_order
    smartapi_get_order_book = _execution_adapter.get_order_book
    smartapi_get_positions = _execution_adapter.get_positions
    smartapi_get_funds = _execution_adapter.get_funds
    resolve_option_contract = getattr(_execution_adapter, "resolve_option_contract", None)
    from brokers.smartapi.client import INDEX_TOKENS as SMARTAPI_INDEX_TOKENS  # noqa: F401
    from brokers.smartapi.history import get_candle_data, get_index_candles  # noqa: F401
    from brokers.smartapi.websocket import EXCHANGE_TYPE, SmartTickStream  # noqa: F401
    from market.quotes.tick_aggregator import TickAggregator  # noqa: F401
else:
    # Public-only mode: NSE/BSE public API is the only data source. Any
    # accidentally reached broker-only path raises instead of logging in.
    MD_PROVIDER_KEYS = ("NSE_BSE",)
    MD_PROVIDER_CAPABILITIES = {
        "NSE_BSE": {"snapshot": True, "websocket": False, "execution": False}
    }

    def md_get_active_provider():
        return "NSE_BSE"

    def md_provider_has_credentials(name):
        return name == "NSE_BSE"

    def md_set_active_provider(name):
        return name

    def md_provider_status():
        return [
            {
                "id": "NSE_BSE",
                "label": "NSE/BSE API",
                "status": "POLLING",
                "active": True,
                "capabilities": dict(MD_PROVIDER_CAPABILITIES["NSE_BSE"]),
            }
        ]

    class _DisabledMarketData:
        index_tokens = staticmethod(lambda: {})
        list_expiries = staticmethod(_disabled)
        get_atm_chain = staticmethod(_disabled)
        find_option_token = staticmethod(_disabled)
        get_batch_quotes = staticmethod(_disabled)
        get_batch_quotes_by_token = staticmethod(_disabled)
        get_spot_quote = staticmethod(_disabled)

    market_data = _DisabledMarketData()
    smartapi_place_order = _disabled
    smartapi_get_order_book = _disabled
    smartapi_get_positions = _disabled
    smartapi_get_funds = _disabled
    resolve_option_contract = None
    get_index_candles = _disabled
    get_candle_data = _disabled
    SMARTAPI_INDEX_TOKENS = {}
    SmartTickStream = None
    TickAggregator = None
    EXCHANGE_TYPE = {}
