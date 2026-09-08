"""Dependency-injected aiohttp application bootstrap."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
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


@web.middleware
async def local_dashboard_only(request, handler):
    """Only the token-authenticated mobile route is available over the LAN."""
    if request.path != "/mobile-ws":
        try:
            address = ipaddress.ip_address(request.remote or "")
            local = address.is_loopback or (
                isinstance(address, ipaddress.IPv6Address)
                and address.ipv4_mapped is not None
                and address.ipv4_mapped.is_loopback
            )
        except ValueError:
            local = False
        if not local:
            return web.Response(status=403, text="Dashboard is local-only")
    return await handler(request)


def mobile_enabled() -> bool:
    return os.getenv("MTERMINALS_MOBILE_WS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def create_app(routes: ServerRoutes, config: ServerConfig) -> web.Application:
    """Build the HTTP application without importing runtime state."""
    app = web.Application(middlewares=[local_dashboard_only, config.middleware])
    if mobile_enabled() and routes.mobile_websocket is not None:
        if not os.getenv("MTERMINALS_MOBILE_TOKEN", "").strip():
            raise RuntimeError("Mobile access requires MTERMINALS_MOBILE_TOKEN")
        app.router.add_get("/mobile-ws", routes.mobile_websocket)
    app.router.add_get("/health", routes.health)
    app.router.add_get("/metrics", routes.metrics)
    app.router.add_get("/ws", routes.websocket)
    app.router.add_get("/bridge", routes.bridge_websocket)
    app.router.add_get("/dashboard-relay", routes.bridge_websocket)
    app.router.add_get("/api/spot-history", routes.spot_history)
    app.router.add_get("/api/history", routes.history)
    app.router.add_get("/api/backtest", routes.backtest)
    app.router.add_get("/api/lot-sizes", routes.lot_sizes)
    app.router.add_get("/api/symbols", routes.symbols)
    app.router.add_get("/api/broker-health", routes.broker_health)
    app.router.add_static("/", path=config.frontend_dir, name="static")
    return app


async def start_http_server(routes: ServerRoutes, config: ServerConfig):
    app = create_app(routes, config)
    runner = web.AppRunner(app)
    await runner.setup()
    host = "0.0.0.0" if mobile_enabled() else config.host
    site = web.TCPSite(runner, host, config.port)
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
