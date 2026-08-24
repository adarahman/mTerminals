"""Stable broker boundary for option-chain fetching and symbol identity."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class BrokerOptionChainAdapter:
    def __init__(
        self,
        *,
        fetch_chain: Callable[..., Any],
        canonicalize_symbol: Callable[[str], str | None],
    ) -> None:
        self._fetch_chain = fetch_chain
        self._canonicalize_symbol = canonicalize_symbol

    def canonicalize(self, symbol: str) -> str:
        normalized = (symbol or "").strip().upper()
        if not normalized:
            return normalized
        try:
            return self._canonicalize_symbol(normalized) or normalized
        except Exception:
            return normalized

    def fetch(
        self,
        symbol: str,
        expiry: str,
        exchange: str,
        strikes_each_side: int,
    ):
        return self._fetch_chain(
            self.canonicalize(symbol),
            expiry,
            exchange=exchange,
            strikes_around_atm=strikes_each_side,
        )
