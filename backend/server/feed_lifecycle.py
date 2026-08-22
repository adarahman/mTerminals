"""Provider-neutral live-feed lifecycle dispatch.

Provider socket implementations remain in their dedicated clients.  The
server supplies callbacks because it owns the process loop and runtime state.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from brokers.provider_registry import normalize_provider


def restart(
    provider: str | None,
    symbol: str,
    expiry: str | None,
    callbacks: Mapping[str, Callable[[str, str | None], Any]],
) -> bool:
    """Request a non-blocking symbol/expiry switch for a supported feed."""
    callback = callbacks.get(normalize_provider(provider))
    if callback is None:
        return False
    callback(symbol, expiry)
    return True


def start(
    provider: str | None,
    loop: Any,
    callbacks: Mapping[str, Callable[[Any], Any]],
    schedule: Callable[[Callable[[Any], Any], Any, str], Any],
) -> bool:
    """Schedule a provider's blocking startup function on the server loop."""
    name = normalize_provider(provider)
    callback = callbacks.get(name)
    if callback is None:
        return False
    schedule(callback, loop, f"{name.lower()}_startup")
    return True


def is_allowed(
    provider: str | None,
    active_data_source: str | None,
    supports_websocket: Callable[[str | None], bool],
) -> bool:
    """Only accept ticks from the current streaming data source."""
    return (
        normalize_provider(provider) == normalize_provider(active_data_source)
        and supports_websocket(active_data_source)
    )


def stop(
    provider: str | None,
    callbacks: Mapping[str, Callable[[], Any]],
    schedule: Callable[[Callable[[], Any]], Any],
) -> bool:
    """Schedule best-effort cleanup for a provider's active subscriptions."""
    callback = callbacks.get(normalize_provider(provider))
    if callback is None:
        return False
    schedule(callback)
    return True
