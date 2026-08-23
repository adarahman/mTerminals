import asyncio

from server.websocket_clients import WebSocketClientHub


class _Client:
    def __init__(self, error=None):
        self.error = error
        self.messages = []

    async def send_str(self, message):
        if self.error:
            raise self.error
        self.messages.append(message)


def test_broadcast_reaches_healthy_clients_and_removes_failed_client():
    hub = WebSocketClientHub()
    healthy = _Client()
    broken = _Client(RuntimeError("socket closed"))
    errors = []
    hub.add(healthy)
    hub.add(broken)

    asyncio.run(hub.broadcast('{"type":"tick"}', on_error=errors.append))

    assert healthy.messages == ['{"type":"tick"}']
    assert broken not in hub.clients
    assert len(errors) == 1
