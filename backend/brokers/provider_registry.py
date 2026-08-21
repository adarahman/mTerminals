"""Canonical broker-provider metadata.

This module owns names and capabilities only.  SDK imports, credentials, and
network calls stay in their provider-specific modules, so reading the registry
is always safe during process startup and status rendering.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    """Static capabilities exposed by one selectable market-data provider."""

    key: str
    label: str
    snapshot: bool
    websocket: bool
    execution: bool

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "snapshot": self.snapshot,
            "websocket": self.websocket,
            "execution": self.execution,
        }


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec("SMARTAPI", "ANGEL ONE", True, True, True),
    ProviderSpec("UPSTOX", "UPSTOX", True, True, True),
    ProviderSpec("SHOONYA", "SHOONYA", True, True, True),
    ProviderSpec("KITE", "ZERODHA", True, False, True),
    ProviderSpec("BREEZE", "ICICI DIRECT", True, False, True),
    ProviderSpec("KOTAK", "KOTAK NEO", True, False, False),
    ProviderSpec("NSE_BSE", "NSE/BSE API", True, False, False),
)

PROVIDERS: dict[str, ProviderSpec] = {spec.key: spec for spec in PROVIDER_SPECS}
PROVIDER_KEYS: tuple[str, ...] = tuple(PROVIDERS)
STREAMING_PROVIDER_KEYS = frozenset(
    spec.key for spec in PROVIDER_SPECS if spec.websocket
)
EXECUTION_PROVIDER_KEYS = frozenset(
    spec.key for spec in PROVIDER_SPECS if spec.execution
)


def normalize_provider(name: str | None) -> str:
    """Return the normalized provider key, without validating it."""
    return (name or "").strip().upper()


def provider_capabilities() -> dict[str, dict[str, bool]]:
    """Compatibility-shaped, defensive capability map for UI consumers."""
    return {key: spec.capabilities for key, spec in PROVIDERS.items()}


def provider_display_names() -> dict[str, str]:
    """Compatibility-shaped display-name map for UI consumers."""
    return {key: spec.label for key, spec in PROVIDERS.items()}


def supports_websocket(name: str | None) -> bool:
    """Whether this build has a normalized tick-stream client for ``name``."""
    spec = PROVIDERS.get(normalize_provider(name))
    return bool(spec and spec.websocket)
