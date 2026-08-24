"""Dashboard WebSocket connection lifecycle."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from aiohttp import web


class DashboardWebSocketHandler:
    """Own transport lifecycle while application operations stay injected."""

    def __init__(
        self,
        *,
        origin_allowed: Callable[[Any], bool],
        clients,
        connected_count: Callable[[], int],
        metrics,
        query_controller,
        send_handshake,
        has_market_payload: Callable[[], bool],
        decode: Callable[[str], Any],
        dispatch_message,
        symbol: Callable[[], str],
        expiry: Callable[[], str | None],
        logger: logging.Logger,
        websocket_factory=web.WebSocketResponse,
    ):
        self._origin_allowed = origin_allowed
        self._clients = clients
        self._connected_count = connected_count
        self._metrics = metrics
        self._query_controller = query_controller
        self._send_handshake = send_handshake
        self._has_market_payload = has_market_payload
        self._decode = decode
        self._dispatch_message = dispatch_message
        self._symbol = symbol
        self._expiry = expiry
        self._logger = logger
        self._websocket_factory = websocket_factory

    async def __call__(self, request):
        if not self._origin_allowed(request):
            print(
                f"[ws] REJECTED — disallowed Origin: "
                f"{request.headers.get('Origin')!r}",
                flush=True,
            )
            return web.Response(status=403, text="Origin not allowed")

        websocket = self._websocket_factory(heartbeat=20)
        await websocket.prepare(request)
        self._clients.add(websocket)
        reconnect = request.query.get("reconnect") == "1"
        self._metrics.websocket_connected(
            self._connected_count(), reconnect=reconnect
        )
        started_at = time.monotonic()
        self._log_connected()

        query_result = await self._query_controller.apply(request.query)
        await self._send_handshake(
            websocket,
            send_full=(
                self._has_market_payload()
                and not query_result.futures_reference_switched
            ),
        )

        try:
            async for message in websocket:
                if message.type == web.WSMsgType.TEXT:
                    try:
                        data = self._decode(message.data)
                    except Exception as exc:
                        print(f"[ws] bad inbound message, ignoring: {exc}", flush=True)
                        continue
                    await self._dispatch_message(data)
                elif message.type in {
                    web.WSMsgType.ERROR,
                    web.WSMsgType.CLOSE,
                    web.WSMsgType.CLOSING,
                    web.WSMsgType.CLOSED,
                }:
                    print(
                        f"[ws] connection ended via {message.type} "
                        f"close_code={websocket.close_code}",
                        flush=True,
                    )
        finally:
            self._clients.discard(websocket)
            self._metrics.websocket_disconnected(self._connected_count())
            self._log_disconnected(websocket, time.monotonic() - started_at)
        return websocket

    def _log_connected(self) -> None:
        self._logger.info(
            "dashboard websocket connected",
            extra={
                "event": "websocket.connected",
                "subsystem": "websocket",
                "status": "connected",
                "connected_clients": self._connected_count(),
                "symbol": self._symbol(),
                "expiry": self._expiry(),
            },
        )

    def _log_disconnected(self, websocket, alive_for: float) -> None:
        self._logger.info(
            "dashboard websocket disconnected",
            extra={
                "event": "websocket.disconnected",
                "subsystem": "websocket",
                "status": "disconnected",
                "connected_clients": self._connected_count(),
                "duration_seconds": round(alive_for, 3),
                "reason": f"close_code={websocket.close_code}",
                "symbol": self._symbol(),
                "expiry": self._expiry(),
            },
        )
