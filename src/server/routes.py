"""HTTP route composition independent of runtime globals."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ServerRoutes:
    health: Callable[..., Any]
    broker_health: Callable[..., Any]
    metrics: Callable[..., Any]
    websocket: Callable[..., Any]
    bridge_websocket: Callable[..., Any]
    spot_history: Callable[..., Any]
    history: Callable[..., Any]
    backtest: Callable[..., Any]
    lot_sizes: Callable[..., Any]
    symbols: Callable[..., Any]

class HttpRouteHandlers:
    """Adapt HTTP requests to injected server services."""

    def __init__(
        self,
        *,
        history_api,
        backtest_response,
        default_symbol: Callable[[], str],
        run_backtest,
        health_response,
        health_snapshot,
        record_health_transition,
        metrics_response,
        metrics,
        symbols,
    ):
        self._history_api = history_api
        self._backtest_response = backtest_response
        self._default_symbol = default_symbol
        self._run_backtest = run_backtest
        self._health_response = health_response
        self._health_snapshot = health_snapshot
        self._record_health_transition = record_health_transition
        self._metrics_response = metrics_response
        self._metrics = metrics
        self._symbols = symbols


    async def spot_history(self, request):
        return await self._history_api.spot_history(request)

    async def history(self, request):
        return await self._history_api.history(request)

    async def lot_sizes(self, request):
        return await self._history_api.lot_sizes(request)

    async def symbols(self, _request):
        from aiohttp import web

        universe = self._symbols() or {}
        values = [
            str(symbol).strip().upper()
            for group in ("indices", "stocks")
            for symbol in (universe.get(group) or [])
            if str(symbol).strip()
        ]
        return web.json_response(sorted(set(values)))

    async def backtest(self, request):
        return await self._backtest_response(
            request,
            default_symbol=self._default_symbol(),
            run_backtest=self._run_backtest,
        )

    async def health(self, request):
        return await self._health_response(
            request,
            snapshot=self._health_snapshot,
            record_transition=self._record_health_transition,
        )

    async def metrics(self, request):
        return await self._metrics_response(request, metrics=self._metrics)
