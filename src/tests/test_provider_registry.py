"""Static provider metadata must agree across broker-routing boundaries."""

from brokers import market_data, market_data_registry
from brokers.provider_registry import (
    EXECUTION_PROVIDER_KEYS,
    PROVIDER_KEYS,
    STREAMING_PROVIDER_KEYS,
    supports_websocket,
)


def test_market_data_uses_canonical_provider_metadata():
    assert market_data.PROVIDER_KEYS == PROVIDER_KEYS
    assert set(market_data.PROVIDER_CAPABILITIES) == set(PROVIDER_KEYS)
    assert set(market_data.PROVIDER_DISPLAY_NAMES) == set(PROVIDER_KEYS)


def test_streaming_and_execution_capabilities_are_explicit():
    assert STREAMING_PROVIDER_KEYS == {"SMARTAPI", "UPSTOX", "SHOONYA", "KOTAK"}
    assert EXECUTION_PROVIDER_KEYS == {
        "SMARTAPI", "UPSTOX", "SHOONYA", "KITE", "BREEZE"
    }
    assert supports_websocket("KOTAK") is True
    assert supports_websocket("NSE_BSE") is False


def test_failed_provider_health_uses_longer_backoff():
    now = 1_000.0
    failed = (now - 600, {"ready": False})
    ready = (now - 600, {"ready": True})

    assert market_data_registry._health_cache_is_fresh(
        failed, now, active=False
    )
    assert not market_data_registry._health_cache_is_fresh(
        ready, now, active=False
    )
    assert supports_websocket("shoonya") is True
