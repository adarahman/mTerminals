"""Common connection boundary for broker adapters.

Broker SDKs authenticate in very different ways (TOTP, daily OAuth token,
or pasted session token), but callers need one small, safe question answered
before changing a runtime provider: *is this adapter usable now?*  This
module provides that question without hiding any broker-specific login flow.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Callable, Optional

from brokers.logging import broker_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectionStatus:
    """Result of a non-mutating connection readiness check."""

    provider: str
    ready: bool
    error: Optional[str] = None


def _adapter_healthcheck(module_name: str) -> Callable[[], tuple[bool, Optional[str]]]:
    """Resolve an adapter lazily so unused broker SDKs stay optional."""
    def check() -> tuple[bool, Optional[str]]:
        module = import_module(module_name)
        healthcheck = getattr(module, "healthcheck", None)
        if healthcheck is None:
            return False, f"{module_name} does not expose healthcheck()"
        return healthcheck()
    return check


# Add an adapter here only after it exposes healthcheck(). Market-data-only
# providers intentionally do not appear: selecting them must never trigger an
# account login.
_CHECKS: dict[str, Callable[[], tuple[bool, Optional[str]]]] = {
    "SHOONYA": _adapter_healthcheck("brokers.shoonya_client"),
}

# One canonical execution route per broker.  Market-data modules stay in
# market_data.py's provider registry; this table is deliberately limited to
# the account/order contract used by ws_server_live.py.
EXECUTION_ADAPTERS: dict[str, str] = {
    "SMARTAPI": "brokers.smartapi_client",
    "UPSTOX": "brokers.upstox_execution_adapter",
    "KITE": "brokers.kite_execution_adapter",
    "SHOONYA": "brokers.shoonya_client",
    "BREEZE": "brokers.breeze_client",
}
_EXECUTION_METHODS = ("place_order", "get_order_book", "get_positions", "get_funds")


def get_execution_adapter(provider: str) -> ModuleType:
    """Load a broker's common order/account adapter on demand.

    Adapters retain broker-specific authentication and symbol resolution, but
    all must expose the same order/account operations. This removes provider
    branches from server startup and makes adding a broker a registry change.
    """
    name = (provider or "").strip().upper()
    module_name = EXECUTION_ADAPTERS.get(name)
    if not module_name:
        raise ValueError(
            f"No execution adapter for {name!r}. Valid: {sorted(EXECUTION_ADAPTERS)}"
        )
    module = import_module(module_name)
    missing = [method for method in _EXECUTION_METHODS if not callable(getattr(module, method, None))]
    if missing:
        raise RuntimeError(f"Execution adapter {module_name} is incomplete: {', '.join(missing)}")
    return module


def check_connection(provider: str) -> ConnectionStatus:
    """Return normalized readiness for a broker adapter.

    Unknown and data-only providers are treated as ready because they have no
    account session to preflight. This keeps a market-data source switch from
    unnecessarily requiring execution credentials.
    """
    name = (provider or "").strip().upper()
    check = _CHECKS.get(name)
    if check is None:
        return ConnectionStatus(provider=name, ready=True)
    try:
        ready, error = check()
        status = ConnectionStatus(provider=name, ready=bool(ready), error=error)
    except Exception as exc:  # optional SDK/import failures must not crash a switch
        status = ConnectionStatus(provider=name, ready=False, error=str(exc))
    broker_event(
        logger,
        provider=name,
        operation="connection",
        status="ready" if status.ready else "unavailable",
        level=logging.INFO if status.ready else logging.WARNING,
        reason=status.error,
    )
    return status
