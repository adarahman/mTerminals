"""Paper portfolio presentation and dashboard publication."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
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


class PaperPortfolioService:
    """Own portfolio snapshots shared by ticks, orders, and handshakes."""

    def __init__(
        self,
        *,
        engine,
        price_book,
        instrument_key: Callable[..., str],
        broadcast: Callable[[dict], Awaitable[None]],
        last_payload: Callable[[], dict | None],
    ) -> None:
        self._engine = engine
        self._price_book = price_book
        self._instrument_key = instrument_key
        self._broadcast = broadcast
        self._last_payload = last_payload

    def current_prices(self) -> dict[str, float]:
        return self._price_book.build(self._last_payload())

    def snapshot(self, current_prices: dict[str, float]) -> tuple[dict, list]:
        portfolio = self._engine.get_portfolio_summary(current_prices)
        spot = current_prices.get(
            self._instrument_key("NIFTY", "", None, "INDEX")
        )
        portfolio["funds"] = self._engine.get_fund_summary(
            spot_price=spot,
            current_prices=current_prices,
        )
        return portfolio, self._engine.get_orders()

    def handshake_snapshot(self) -> tuple[dict, list]:
        return self.snapshot(self.current_prices())

    async def broadcast(self, current_prices: dict[str, float]) -> None:
        portfolio, orders = self.snapshot(current_prices)
        await self._broadcast({"type": "portfolio", "payload": portfolio})
        await self._broadcast({"type": "orders", "payload": orders})

    async def broadcast_from_feed(self, payload: dict[str, Any]) -> None:
        current_prices = self._price_book.build(payload)
        self._engine.check_pending_orders(current_prices)
        await self.broadcast(current_prices)
