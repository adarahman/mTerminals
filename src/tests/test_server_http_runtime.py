import asyncio

from server.http_runtime import build_http_runtime


async def _handler(_request):
    return None


def test_build_http_runtime_maps_handlers_and_configuration():
    middleware = object()
    selection = {"symbol": "NIFTY"}
    runtime = build_http_runtime(
        health=_handler,
        broker_health=_handler,
        metrics=_handler,
        websocket=_handler,
        bridge_websocket=_handler,
        spot_history=_handler,
        history=_handler,
        backtest=_handler,
        lot_sizes=_handler,
        host="127.0.0.1",
        port=5500,
        symbol=lambda: selection["symbol"],
        middleware=middleware,
    )

    assert runtime.routes.health is _handler
    assert runtime.routes.broker_health is _handler
    assert runtime.routes.bridge_websocket is _handler
    assert runtime.routes.lot_sizes is _handler
    assert runtime.config.host == "127.0.0.1"
    assert runtime.config.port == 5500
    assert runtime.config.symbol == "NIFTY"
    assert runtime.config.middleware is middleware

    selection["symbol"] = "BANKNIFTY"
    assert runtime.config.symbol == "BANKNIFTY"


def test_http_runtime_starts_with_assembled_dependencies():
    calls = []

    async def starter(routes, config):
        calls.append((routes, config))
        return "runner"

    runtime = build_http_runtime(
        health=_handler,
        broker_health=_handler,
        metrics=_handler,
        websocket=_handler,
        bridge_websocket=_handler,
        spot_history=_handler,
        history=_handler,
        backtest=_handler,
        lot_sizes=_handler,
        host="localhost",
        port=5500,
        symbol=lambda: "NIFTY",
        middleware=object(),
        starter=starter,
    )

    assert asyncio.run(runtime.start()) == "runner"
    assert calls == [(runtime.routes, runtime.config)]
