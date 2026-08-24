"""Dependency-injected aiohttp application bootstrap."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from aiohttp import web
from server.routes import ServerRoutes


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    symbol: str
    middleware: Callable[..., Any]
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"


def create_app(routes: ServerRoutes, config: ServerConfig) -> web.Application:
    """Build the HTTP application without importing runtime state."""
    app = web.Application(middlewares=[config.middleware])
    app.router.add_get("/health", routes.health)
    app.router.add_get("/metrics", routes.metrics)
    app.router.add_get("/ws", routes.websocket)
    app.router.add_get("/bridge", routes.bridge_websocket)
    app.router.add_get("/dashboard-relay", routes.bridge_websocket)
    app.router.add_get("/api/spot-history", routes.spot_history)
    app.router.add_get("/api/history", routes.history)
    app.router.add_get("/api/backtest", routes.backtest)
    app.router.add_get("/api/lot-sizes", routes.lot_sizes)
    app.router.add_get("/api/broker-health", routes.broker_health)
    app.router.add_static("/", path=config.frontend_dir, name="static")
    return app


async def start_http_server(routes: ServerRoutes, config: ServerConfig):
    app = create_app(routes, config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port)
    await site.start()

    print(f"[http] serving static files at http://{config.host}:{config.port}/")
    print(
        "[http] Dashboard available at "
        f"http://{config.host}:{config.port}/dist/Dashboard/DashboardPro.html"
    )
    print(
        f"[ws] WebSocket endpoint at ws://{config.host}:{config.port}/ws "
        f"symbol={config.symbol}"
    )
    return runner
