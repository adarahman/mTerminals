"""Small, protocol-agnostic registry for WebSocket clients.

The live server owns authentication, message shapes, and connection-specific
side effects.  This module only owns the repetitive client-set lifecycle and
best-effort fan-out, so unrelated WebSocket endpoints do not copy it.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class WebSocketClientHub:
    """Track WebSocket clients and broadcast pre-serialized messages safely."""

    def __init__(self) -> None:
        self.clients: set[Any] = set()

    def add(self, client: Any) -> None:
        self.clients.add(client)

    def discard(self, client: Any) -> None:
        self.clients.discard(client)

    def __len__(self) -> int:
        return len(self.clients)

    async def broadcast(
        self,
        message: str,
        *,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Send one serialized message to every current client.

        A failing client is removed without preventing healthy clients from
        receiving the same update.  Snapshotting the set also permits a
        disconnect to happen while sends are in flight.
        """
        if not self.clients:
            return
        clients = tuple(self.clients)
        results = await asyncio.gather(
            *(client.send_str(message) for client in clients), return_exceptions=True
        )
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self.clients.discard(client)
                if on_error is not None:
                    on_error(result)
