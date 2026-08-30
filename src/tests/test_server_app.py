from pathlib import Path

from server.http_app import ServerConfig, create_app
from server.routes import ServerRoutes


async def _handler(_request):
    return None


async def _middleware(request, handler):
    return await handler(request)


def test_create_app_registers_routes_without_runtime_launcher(tmp_path: Path):
    routes = ServerRoutes(*([_handler] * 10))
    config = ServerConfig(
        host="127.0.0.1",
        port=8765,
        symbol="NIFTY",
        middleware=_middleware,
        frontend_dir=tmp_path,
    )

    app = create_app(routes, config)
    paths = {resource.canonical for resource in app.router.resources()}

    assert {
        "/health",
        "/metrics",
        "/ws",
        "/bridge",
        "/dashboard-relay",
        "/api/spot-history",
        "/api/history",
        "/api/backtest",
        "/api/lot-sizes",
        "/api/symbols",
        "",
    } <= paths
