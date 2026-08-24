"""
Runtime market-data provider registry.

Owns:
- active provider selection
- fallback wrapping
- credential availability checks
- runtime switching
- provider status for the UI

Static provider metadata stays in brokers.provider_registry.
Actual provider implementations stay in brokers.market_data.
"""

import logging

from infrastructure.config import settings as _md_settings
from brokers.connection import check_connection
from brokers.logging import broker_event
from brokers.provider_registry import (
    PROVIDER_KEYS,
    normalize_provider,
    provider_capabilities,
    provider_display_names,
)

logger = logging.getLogger(__name__)

PROVIDER_CAPABILITIES: dict[str, dict] = provider_capabilities()
PROVIDER_DISPLAY_NAMES: dict[str, str] = provider_display_names()


def _provider_classes():
    from brokers.breeze.adapter import BreezeMarketData
    from brokers.kite.market_data import KiteMarketData
    from brokers.kotak.adapter import KotakMarketData
    from brokers.shoonya.adapter import ShoonyaMarketData
    from brokers.smartapi.market_data import SmartApiMarketData
    from brokers.upstox.market_data import UpstoxMarketData
    from market.providers.fallback import FallbackMarketData
    from market.providers.nse_bse import NseBseMarketData

    providers = {
        "SMARTAPI": SmartApiMarketData,
        "UPSTOX": UpstoxMarketData,
        "SHOONYA": ShoonyaMarketData,
        "KITE": KiteMarketData,
        "BREEZE": BreezeMarketData,
        "KOTAK": KotakMarketData,
        "NSE_BSE": NseBseMarketData,
    }

    return providers, FallbackMarketData


def provider_has_credentials(name: str) -> bool:
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
        return bool(
            s.shoonya_user_id
            and s.shoonya_password
            and s.shoonya_totp_secret
        )

    if name == "BREEZE":
        return bool(
            s.breeze_api_key
            and s.breeze_api_secret
            and s.breeze_api_session
        )

    if name == "KOTAK":
        return bool(
            s.kotak_consumer_key
            and s.kotak_mobile
            and s.kotak_ucc
            and s.kotak_totp_secret
            and s.kotak_mpin
        )

    return False


_PROVIDERS, _FallbackMarketData = _provider_classes()

_primary_name = (
    _md_settings.market_data_provider
    if _md_settings.market_data_provider in _PROVIDERS
    else "SMARTAPI"
)

_fallback_name = _md_settings.market_data_fallback_provider


def _build_instance(name: str):
    primary_cls = _PROVIDERS[name]
    primary = primary_cls()

    if (
        _fallback_name
        and _fallback_name in _PROVIDERS
        and _fallback_name != name
    ):
        fallback_cls = _PROVIDERS[_fallback_name]
        fallback = fallback_cls()

        return _FallbackMarketData(
            primary,
            fallback,
            primary_name=name,
            fallback_name=_fallback_name,
        )

    return primary


_active_provider_name = _primary_name
_active_provider_instance = _build_instance(_primary_name)


class _SwitchingMarketData:
    def __getattr__(self, name):
        return getattr(_active_provider_instance, name)

    def __repr__(self):
        return f"<SwitchingMarketData active={_active_provider_name!r}>"


market_data = _SwitchingMarketData()


def get_active_provider() -> str:
    return _active_provider_name


def set_active_provider(name: str) -> bool:
    global _active_provider_name, _active_provider_instance

    name = normalize_provider(name)

    if name not in _PROVIDERS:
        raise ValueError(
            f"Unknown market-data provider {name!r}. "
            f"Valid: {sorted(_PROVIDERS)}"
        )

    if name == _active_provider_name:
        return True

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

    candidate = _build_instance(name)

    _active_provider_name = name
    _active_provider_instance = candidate

    broker_event(
        logger,
        provider=name,
        operation="provider_switch",
        status="active",
    )

    logger.info(
        "[market_data] active provider switched to %s",
        name,
    )

    return True


def provider_status() -> list[dict]:
    out = []

    for key in PROVIDER_KEYS:
        caps = PROVIDER_CAPABILITIES[key]

        # NSE/BSE is public market-data fallback, not an account broker
        if key == "NSE_BSE":
            out.append(
                {
                    "id": key,
                    "label": PROVIDER_DISPLAY_NAMES.get(key, key),
                    "status": "POLLING",
                    "active": key == _active_provider_name,
                    "ready": True,
                    "error": None,
                    "capabilities": caps,
                }
            )
            continue

        connection = check_connection(key)

        status_map = {
            "available": "AVAILABLE",
            "auth_failed": "AUTH_FAILED",
            "session_expired": "SESSION_EXPIRED",
            "api_unavailable": "API_UNAVAILABLE",
            "unknown": "UNKNOWN",
        }

        out.append(
            {
                "id": key,
                "label": PROVIDER_DISPLAY_NAMES.get(key, key),
                "status": status_map.get(
                    connection.status.value,
                    "UNKNOWN"
                ),
                "active": key == _active_provider_name,
                "ready": connection.ready,
                "error": connection.error,
                "capabilities": caps,
            }
        )

    return out
