"""HTTP route and bind configuration assembly for the live server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from server.http_app import ServerConfig, start_http_server
from server.routes import ServerRoutes


@dataclass(frozen=True)
class HttpRuntime:
    """A fully assembled HTTP server that can be started by the lifecycle."""

    routes: ServerRoutes
    host: str
    port: int
    symbol: Callable[[], str]
    middleware: Any
    starter: Callable[[ServerRoutes, ServerConfig], Awaitable[Any]] = start_http_server

    @property
    def config(self) -> ServerConfig:
        return ServerConfig(
            host=self.host,
            port=self.port,
            symbol=self.symbol(),
            middleware=self.middleware,
        )

    async def start(self):
        return await self.starter(self.routes, self.config)


def build_http_runtime(
    *,
    health,
    broker_health,
    metrics,
    websocket,
    bridge_websocket,
    spot_history,
    history,
    backtest,
    lot_sizes,
    host: str,
    port: int,
    symbol: Callable[[], str],
    middleware,
    starter=start_http_server,
) -> HttpRuntime:
    """Assemble HTTP routes and bind configuration from app-level handlers."""
    return HttpRuntime(
        routes=ServerRoutes(
            health=health,
            broker_health=broker_health,
            metrics=metrics,
            websocket=websocket,
            bridge_websocket=bridge_websocket,
            spot_history=spot_history,
            history=history,
            backtest=backtest,
            lot_sizes=lot_sizes,
        ),
        host=host,
        port=port,
        symbol=symbol,
        middleware=middleware,
        starter=starter,
    )
