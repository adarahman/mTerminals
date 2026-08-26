"""Broker-facing expiry lookup adapter with canonical normalization."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
import logging
import threading
import time

from brokers.market_data_registry import get_active_provider, market_data


logger = logging.getLogger(__name__)

_DIRECT_EXPIRY_PROVIDERS = frozenset(
    {"UPSTOX", "SHOONYA", "KITE", "BREEZE", "KOTAK"}
)
_EXPIRY_FORMATS = ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d")


class BrokerExpiryAdapter:
    def __init__(
        self,
        *,
        fallback: Callable[[str], Iterable[str]],
        active_provider: Callable[[], str] = get_active_provider,
        provider_market_data=market_data,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        self._fallback = fallback
        self._active_provider = active_provider
        self._provider_market_data = provider_market_data
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[tuple[str, str, str], tuple[float, tuple[str, ...]]] = {}
        self._cache_lock = threading.Lock()

    def list_expiries(self, symbol: str, exchange: str) -> list[str]:
        provider = self._active_provider()
        key = (provider, symbol.strip().upper(), exchange.strip().upper())
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] < self._cache_ttl_seconds:
                return list(cached[1])

        if provider in _DIRECT_EXPIRY_PROVIDERS:
            try:
                offered = self._provider_market_data.list_expiries(
                    symbol, exchange=exchange
                )
                normalized = [
                    expiry
                    for value in offered
                    if (expiry := self._normalize(value)) is not None
                ]
                if normalized:
                    with self._cache_lock:
                        self._cache[key] = (now, tuple(normalized))
                    return normalized
            except Exception as exc:
                logger.warning(
                    "[Expiry] %s expiry lookup failed for %s: %s",
                    provider,
                    symbol,
                    exc,
                )
        fallback = list(self._fallback(symbol))
        with self._cache_lock:
            self._cache[key] = (now, tuple(fallback))
        return fallback

    @staticmethod
    def _normalize(value) -> str | None:
        normalized = str(value or "").strip().upper()
        for date_format in _EXPIRY_FORMATS:
            try:
                parsed = datetime.strptime(normalized, date_format)
                return parsed.strftime("%d-%b-%Y")
            except ValueError:
                continue
        return None
