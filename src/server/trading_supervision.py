"""Live-trading status and reconciliation presentation."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from risk.account_guard import open_lots_from_positions


class LiveTradingSupervisor:
    """Build supervisory status and publish reconciliation alerts."""

    def __init__(
        self,
        *,
        account_guard,
        auto_executor,
        live_orders,
        reconciler,
        lot_sizes,
        cached_positions: Callable[[], object],
        symbol: Callable[[], str],
        broker_label: Callable[[], str],
        live_trading_enabled: bool,
        max_lots_per_order: int,
        max_orders_per_minute: int,
        store_alert: Callable[[dict], None],
        broadcast: Callable[[dict], Awaitable[None]],
        report: Callable[[str], None] = print,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._account_guard = account_guard
        self._auto_executor = auto_executor
        self._live_orders = live_orders
        self._reconciler = reconciler
        self._lot_sizes = lot_sizes
        self._cached_positions = cached_positions
        self._symbol = symbol
        self._broker_label = broker_label
        self._live_trading_enabled = live_trading_enabled
        self._max_lots_per_order = max_lots_per_order
        self._max_orders_per_minute = max_orders_per_minute
        self._store_alert = store_alert
        self._broadcast = broadcast
        self._report = report
        self._clock = clock

    def build_status(self) -> dict:
        guard_status = self._account_guard.get_status()
        positions = self._cached_positions()
        try:
            guard_status["current_open_lots"] = (
                open_lots_from_positions(positions, self._lot_sizes)
                if positions is not None
                else None
            )
        except Exception as exc:
            self._report(
                f"[algo-status] could not compute open lots from cached positions: {exc}"
            )
            guard_status["current_open_lots"] = None

        symbol = self._symbol()
        executor_status = self._auto_executor.get_status(symbol)
        executor_status["history"] = self._auto_executor.get_history()[:30]
        return {
            "broker": self._broker_label(),
            "liveTradingEnabled": self._live_trading_enabled,
            "killSwitchActive": self._live_orders.kill_switch_active(),
            "maxLotsPerOrder": self._max_lots_per_order,
            "maxOrdersPerMinute": self._max_orders_per_minute,
            "accountGuard": guard_status,
            "autoExecutor": executor_status,
            "symbol": symbol,
        }

    async def publish_reconciliation_alert(self, result, source: str) -> None:
        if result.clean:
            return
        payload = {
            "ts": self._clock(),
            "source": source,
            "tripped": result.max_abs_diff_lots() >= self._reconciler.trip_lots,
            "tripLots": self._reconciler.trip_lots,
            "mismatches": [
                {
                    "symbol": mismatch.symbol,
                    "orderBookLots": mismatch.order_book_lots,
                    "positionLots": mismatch.position_lots,
                    "diffLots": mismatch.diff_lots,
                }
                for mismatch in result.mismatches
            ],
            "unparseableSymbols": result.unparseable_symbols,
        }
        self._store_alert(payload)
        await self._broadcast({"type": "reconciliationAlert", "payload": payload})
