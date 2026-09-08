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


def test_shared_mobile_route_requires_token(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setenv('MTERMINALS_MOBILE_WS_ENABLED', 'true')
    monkeypatch.delenv('MTERMINALS_MOBILE_TOKEN', raising=False)
    routes = ServerRoutes(*([_handler] * 10), mobile_websocket=_handler)
    config = ServerConfig('127.0.0.1', 5500, 'NIFTY', _middleware, tmp_path)
    with pytest.raises(RuntimeError, match='TOKEN'):
        create_app(routes, config)
    monkeypatch.setenv('MTERMINALS_MOBILE_TOKEN', 'test-only')
    app = create_app(routes, config)
    assert '/mobile-ws' in {r.canonical for r in app.router.resources()}
    monkeypatch.setenv('MTERMINALS_MOBILE_WS_ENABLED', 'false')
    app = create_app(routes, config)
    assert '/mobile-ws' not in {r.canonical for r in app.router.resources()}


def test_lan_access_is_limited_to_mobile_route():
    import asyncio
    from types import SimpleNamespace
    from server.http_app import local_dashboard_only

    async def handler(_request):
        return 'allowed'

    for remote in ['127.0.0.1', '::1', '::ffff:127.0.0.1']:
        assert asyncio.run(local_dashboard_only(SimpleNamespace(remote=remote, path='/ws'), handler)) == 'allowed'
    for path in ['/ws', '/bridge', '/api/history', '/health', '/']:
        response = asyncio.run(local_dashboard_only(SimpleNamespace(remote='192.168.1.10', path=path), handler))
        assert response.status == 403
    assert asyncio.run(local_dashboard_only(SimpleNamespace(remote='192.168.1.10', path='/mobile-ws'), handler)) == 'allowed'
