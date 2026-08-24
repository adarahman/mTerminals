"""Validation and application of dashboard WebSocket query controls."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QueryControlResult:
    futures_reference_switched: bool = False


class WebSocketQueryController:
    PRICE_SOURCES = frozenset({"AUTO", "EQ", "FUT"})
    FUTURES_EXPIRIES = frozenset({"NEAR", "NEXT", "FAR"})

    def __init__(
        self,
        *,
        current_symbol: Callable[[], str],
        switch_symbol: Callable[[str, str | None], Any],
        switch_data_source: Callable[[str], Awaitable[Any]],
        current_price_source: Callable[[], str],
        set_price_source: Callable[[str], Any],
        current_futures_expiry: Callable[[], str],
        set_futures_expiry: Callable[[str], Any],
        invalidate_market_baseline: Callable[[], Any],
    ):
        self._current_symbol = current_symbol
        self._switch_symbol = switch_symbol
        self._switch_data_source = switch_data_source
        self._current_price_source = current_price_source
        self._set_price_source = set_price_source
        self._current_futures_expiry = current_futures_expiry
        self._set_futures_expiry = set_futures_expiry
        self._invalidate_market_baseline = invalidate_market_baseline

    async def apply(self, query: Mapping[str, str]) -> QueryControlResult:
        symbol = query.get("symbol")
        expiry = query.get("expiry")
        if symbol or expiry:
            self._switch_symbol(symbol or self._current_symbol(), expiry)

        data_source = query.get("dataSource")
        if data_source:
            try:
                await self._switch_data_source(data_source)
            except ValueError as exc:
                print(
                    f"[ws] ignoring invalid ?dataSource={data_source!r}: {exc}",
                    flush=True,
                )

        price_source = query.get("priceSource")
        if price_source:
            normalized = price_source.strip().upper()
            if normalized not in self.PRICE_SOURCES:
                print(
                    f"[ws] ignoring invalid ?priceSource={price_source!r} "
                    "(must be AUTO, EQ or FUT)",
                    flush=True,
                )
            elif normalized != self._current_price_source():
                print(
                    f"[ws] price source switch requested: "
                    f"{self._current_price_source()} -> {normalized}",
                    flush=True,
                )
                self._set_price_source(normalized)
                self._invalidate_market_baseline()

        futures_switched = False
        futures_expiry = query.get("futuresExpiry")
        if futures_expiry:
            normalized = futures_expiry.strip().upper()
            if normalized not in self.FUTURES_EXPIRIES:
                print(
                    f"[ws] ignoring invalid ?futuresExpiry={futures_expiry!r} "
                    "(must be NEAR, NEXT, or FAR)",
                    flush=True,
                )
            elif normalized != self._current_futures_expiry():
                print(
                    f"[ws] futures expiry switch requested: "
                    f"{self._current_futures_expiry()} -> {normalized}",
                    flush=True,
                )
                self._set_futures_expiry(normalized)
                self._invalidate_market_baseline()
                futures_switched = True

        return QueryControlResult(futures_reference_switched=futures_switched)
