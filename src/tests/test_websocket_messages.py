import asyncio

from server.websocket_messages import WebSocketMessageRouter


def _run(coro):
    return asyncio.run(coro)


def _router(calls):
    async def place(payload):
        calls.append(("place", payload))

    async def broadcast(prices):
        calls.append(("broadcast", prices))

    return WebSocketMessageRouter(
        place_order=place,
        cancel_order=lambda order_id: calls.append(("cancel", order_id)) or True,
        broadcast_portfolio=broadcast,
        build_current_prices=lambda payload: {"snapshot": payload},
        last_payload=lambda: "latest",
        start_funds_polling=lambda: calls.append(("funds", "start")),
        stop_funds_polling=lambda: calls.append(("funds", "stop")),
    )


def test_routes_order_and_cancel_messages():
    calls = []
    router = _router(calls)

    _run(router.dispatch({"type": "place_order", "payload": {"side": "BUY"}}))
    _run(router.dispatch({"type": "cancel_order", "payload": {"order_id": "O1"}}))

    assert calls == [
        ("place", {"side": "BUY"}),
        ("cancel", "O1"),
        ("broadcast", {"snapshot": "latest"}),
    ]


def test_routes_live_mode_and_ignores_unknown_messages():
    calls = []
    router = _router(calls)

    _run(router.dispatch({"type": "toggle_live_mode", "payload": {"enabled": True}}))
    _run(router.dispatch({"type": "toggle_live_mode", "payload": {"enabled": False}}))
    _run(router.dispatch({"type": "unknown"}))
    _run(router.dispatch(["invalid-shape"]))

    assert calls == [("funds", "start"), ("funds", "stop")]
