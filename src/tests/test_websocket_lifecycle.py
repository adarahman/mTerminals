import asyncio
import json
import logging
from types import SimpleNamespace

from aiohttp import web

from server.websocket import DashboardWebSocketHandler


class _Metrics:
    def __init__(self):
        self.events = []

    def websocket_connected(self, count, reconnect=False):
        self.events.append(("connected", count, reconnect))

    def websocket_disconnected(self, count):
        self.events.append(("disconnected", count))


class _QueryController:
    async def apply(self, query):
        return SimpleNamespace(futures_reference_switched=query.get("future") == "changed")


class _WebSocket:
    def __init__(self, messages):
        self._messages = messages
        self.close_code = 1000
        self.prepared = False

    async def prepare(self, _request):
        self.prepared = True

    def __aiter__(self):
        self._iterator = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


def test_connection_lifecycle_dispatches_and_cleans_up_clients():
    clients = set()
    metrics = _Metrics()
    dispatched = []
    handshakes = []
    websocket = _WebSocket(
        [SimpleNamespace(type=web.WSMsgType.TEXT, data=json.dumps({"type": "ping"}))]
    )

    async def handshake(_websocket, *, send_full):
        handshakes.append(send_full)

    async def dispatch(data):
        dispatched.append(data)

    handler = DashboardWebSocketHandler(
        origin_allowed=lambda _request: True,
        clients=clients,
        connected_count=lambda: len(clients),
        metrics=metrics,
        query_controller=_QueryController(),
        send_handshake=handshake,
        has_market_payload=lambda: True,
        decode=json.loads,
        dispatch_message=dispatch,
        symbol=lambda: "NIFTY",
        expiry=lambda: None,
        logger=logging.getLogger("test.websocket"),
        websocket_factory=lambda **_kwargs: websocket,
    )
    request = SimpleNamespace(query={"reconnect": "1"}, headers={})

    result = asyncio.run(handler(request))

    assert result is websocket
    assert clients == set()
    assert metrics.events == [("connected", 1, True), ("disconnected", 0)]
    assert handshakes == [True]
    assert dispatched == [{"type": "ping"}]


def test_rejects_disallowed_origin_before_socket_creation():
    handler = DashboardWebSocketHandler(
        origin_allowed=lambda _request: False,
        clients=set(),
        connected_count=lambda: 0,
        metrics=_Metrics(),
        query_controller=_QueryController(),
        send_handshake=None,
        has_market_payload=lambda: False,
        decode=json.loads,
        dispatch_message=None,
        symbol=lambda: "NIFTY",
        expiry=lambda: None,
        logger=logging.getLogger("test.websocket"),
        websocket_factory=lambda **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    response = asyncio.run(handler(SimpleNamespace(query={}, headers={"Origin": "bad"})))

    assert response.status == 403
