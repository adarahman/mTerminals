from unittest.mock import Mock

import pandas as pd

from server.bridge import DashboardBridge


def _bridge(broker_fetcher, public_fetcher=None):
    public_fetcher = public_fetcher or Mock()
    return DashboardBridge(
        state=lambda: {
            "symbol": "NIFTY",
            "futures_expiry": "NEXT",
            "use_smartapi": True,
            "last_payload": None,
            "index_quotes": {},
        },
        origin_allowed=lambda _request: True,
        json_default=lambda value: value,
        market_api=Mock(),
        broker_futures_fetcher=broker_fetcher,
        public_futures_fetcher=public_fetcher,
    )


def test_bridge_uses_injected_futures_capability():
    fetcher = Mock(
        return_value=pd.DataFrame(
            [{"LTP": 25100, "Change": 25, "PctChange": 0.1}]
        )
    )

    quote = _bridge(fetcher)._fetch_futures()

    fetcher.assert_called_once_with("NIFTY", "NEXT")
    assert quote["label"] == "NIFTY FUT (CUR)"
    assert quote["val"] == "25,100.00"


def test_bridge_routes_public_futures_without_broker_fetch():
    broker_fetcher = Mock()
    public_fetcher = Mock(
        return_value=pd.DataFrame(
            [{"LTP": 25000, "Change": -10, "PctChange": -0.04}]
        )
    )
    bridge = _bridge(broker_fetcher, public_fetcher)
    bridge._state = lambda: {
        "symbol": "NIFTY",
        "futures_expiry": "NEAR",
        "use_smartapi": False,
        "last_payload": None,
        "index_quotes": {},
    }

    quote = bridge._fetch_futures()

    public_fetcher.assert_called_once_with("NIFTY", "NEAR")
    broker_fetcher.assert_not_called()
    assert quote["val"] == "25,000.00"


def test_shared_http_port_authenticates_mobile_and_delivers_snapshots(monkeypatch, tmp_path):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from server.http_app import create_app, ServerConfig
    from server.routes import ServerRoutes

    monkeypatch.setenv('MTERMINALS_MOBILE_WS_ENABLED', 'true')
    monkeypatch.setenv('MTERMINALS_MOBILE_TOKEN', 'integration-test-token')

    bridge = _bridge(Mock())
    bridge.snapshot = lambda: {'market': {'symbol': 'NIFTY', 'spot': 25000}}

    async def switch_data_source(source):
        assert source == "KOTAK"
        return False

    bridge.configure_mobile_controls(
        switch_data_source=switch_data_source,
        switch_symbol=Mock(),
    )

    async def handler(_request):
        return web.json_response({'status': 'ok'})

    @web.middleware
    async def middleware(request, handler):
        return await handler(request)

    async def scenario():
        routes = ServerRoutes(*([handler] * 10), mobile_websocket=bridge.handle_mobile)
        app = create_app(routes, ServerConfig('127.0.0.1', 0, 'NIFTY', middleware, tmp_path))

        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/health')).status == 200
            assert (await client.get('/mobile-ws')).status == 401
            assert (await client.get('/mobile-ws?token=wrong')).status == 401

            async with client.ws_connect('/mobile-ws?token=integration-test-token') as ws:
                assert (await ws.receive_json(timeout=2))['market']['spot'] == 25000

                await bridge.broadcast({'market': {'symbol': 'NIFTY', 'spot': 25010}})

                assert (await ws.receive_json(timeout=2))['market']['spot'] == 25010

                await ws.send_json({'action': 'place_order'})
                assert (await ws.receive_json(timeout=2))['type'] == 'control_error'

                # Patch health only after the authenticated WebSocket is established.
                monkeypatch.setattr(
                    "brokers.market_data_registry.get_provider_health",
                    lambda source: {
                        "id": source,
                        "status": "AUTH_FAILED",
                        "ready": False,
                        "error": "access token expired",
                    },
                )

                await ws.send_json({
                    'type': 'switch_data_source',
                    'dataSource': 'KOTAK',
                })

                response = await ws.receive_json(timeout=2)

                assert response['type'] == 'control_ack'
                assert response['action'] == 'switch_data_source'
                assert response['dataSource'] == 'KOTAK'
                assert response['result'] is False
                assert response['status'] == 'AUTH_FAILED'
                assert response['ready'] is False
                assert response['error'] == 'access token expired'

    asyncio.run(scenario())
