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
import time

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

# Last real connectivity result per provider. We never log a broker in just to
# render the dropdown, but whenever we DO probe one (the active check, or a
# switch attempt that succeeds or fails), we remember it so the panel can show
# an honest status instead of a fabricated "ready" dot. Probed-and-down brokers
# render red; brokers never probed render UNKNOWN (grey) — never a false green.
_PROVIDER_HEALTH_CACHE: dict[str, tuple[float, dict]] = {}

# How long a real connectivity probe result is trusted before we re-probe a
# non-active broker. Keeps the dropdown showing live health for every
# configured broker without firing a login/TOTP storm on every render.
_PROVIDER_HEALTH_TTL = 180.0
_ACTIVE_PROVIDER_HEALTH_TTL = 30.0
_UNAVAILABLE_PROVIDER_HEALTH_TTL = 900.0

_STATUS_MAP = {
    "available": "AVAILABLE",
    "auth_failed": "AUTH_FAILED",
    "session_expired": "SESSION_EXPIRED",
    "api_unavailable": "API_UNAVAILABLE",
    "unknown": "UNKNOWN",
}


def _health_from_connection(name: str, connection) -> dict:
    return {
        "id": name,
        "label": PROVIDER_DISPLAY_NAMES.get(name, name),
        "status": _STATUS_MAP.get(connection.status.value, "UNKNOWN"),
        "active": False,
        "ready": connection.ready,
        "error": connection.error,
        "capabilities": PROVIDER_CAPABILITIES.get(name, {}),
    }


def _health_cache_is_fresh(cached, now: float, *, active: bool) -> bool:
    if cached is None:
        return False
    checked_at, entry = cached
    if not entry.get("ready"):
        ttl = _UNAVAILABLE_PROVIDER_HEALTH_TTL
    elif active:
        ttl = _ACTIVE_PROVIDER_HEALTH_TTL
    else:
        ttl = _PROVIDER_HEALTH_TTL
    return now - checked_at < ttl


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
    _PROVIDER_HEALTH_CACHE[name] = (time.time(), _health_from_connection(name, connection))

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

    # optional runtime validation
    if hasattr(candidate, "health_check"):
        if not candidate.health_check():
            broker_event(
                logger,
                provider=name,
                operation="provider_switch",
                status="rejected",
                level=logging.WARNING,
                reason="provider health check failed",
            )
            return False

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
    now = time.time()

    for key in PROVIDER_KEYS:
        caps = PROVIDER_CAPABILITIES[key]

        # NSE/BSE is public market-data fallback, not an account broker.
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

        cached = _PROVIDER_HEALTH_CACHE.get(key)
        active = key == _active_provider_name
        if _health_cache_is_fresh(cached, now, active=active):
            entry = dict(cached[1])
        elif active:
            # Keep active status current without turning every UI refresh
            # into a broker API call.
            connection = check_connection(key)
            entry = _health_from_connection(key, connection)
            _PROVIDER_HEALTH_CACHE[key] = (now, entry)
        else:
            if provider_has_credentials(key):
                # Configured broker: probe for real status, but throttled by the
                # TTL above so we don't log in / fire TOTP on every render.
                try:
                    connection = check_connection(key)
                except Exception:
                    connection = None
                if connection is not None:
                    entry = _health_from_connection(key, connection)
                else:
                    entry = {
                        "id": key,
                        "label": PROVIDER_DISPLAY_NAMES.get(key, key),
                        "status": "UNKNOWN",
                        "active": False,
                        "ready": False,
                        "error": None,
                        "capabilities": caps,
                    }
                _PROVIDER_HEALTH_CACHE[key] = (now, entry)
            else:
                # Not configured: never attempt a login, honestly unknown.
                entry = {
                    "id": key,
                    "label": PROVIDER_DISPLAY_NAMES.get(key, key),
                    "status": "UNKNOWN",
                    "active": False,
                    "ready": False,
                    "error": None,
                    "capabilities": caps,
                }

        entry = dict(entry)
        entry["active"] = active
        out.append(entry)

    return out
