"""Paper-portfolio pricing derived from canonical dashboard payloads."""
from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any


class PaperPriceBook:
    def __init__(
        self,
        last_known: MutableMapping[str, Any],
        instrument_key: Callable[[str, str, Any, str], str],
    ) -> None:
        self._last_known = last_known
        self._instrument_key = instrument_key

    def build(self, payload) -> dict:
        prices = {}
        if not payload or not payload.get("symbol"):
            return dict(self._last_known)
        symbol = payload["symbol"]
        spot = payload.get("spot")
        if spot is not None:
            prices[self._instrument_key(symbol, "", None, "INDEX")] = spot
        expiry = payload.get("expiry") or ""
        future = payload.get("futLTP")
        if future is not None:
            prices[self._instrument_key(symbol, expiry, None, "FUT")] = future
        chains = payload.get("chains") or {}
        if not chains and expiry:
            chains = {expiry: payload.get("chain") or []}
        for chain_expiry, rows in chains.items():
            for row in rows or []:
                strike = row.get("strike")
                if strike is None:
                    continue
                for option_type, field in (("CE", "ceLTP"), ("PE", "peLTP")):
                    if row.get(field) is not None:
                        key = self._instrument_key(
                            symbol, chain_expiry, strike, option_type
                        )
                        prices[key] = row[field]
        self._last_known.update(prices)
        return dict(self._last_known)
