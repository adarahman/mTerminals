"""Kotak Neo live-tick stream — async SFeed WebSocket (neo_api_client >= 2.2.0).

Kotak Neo's NEO Trade API removed the callback-based subscribe()/on_message
WebSocket in 2.2.0. The current SDK exposes only the async/await SFeed feed
(neo_api_client.websocket.feed.SFeedWebSocket), entered via
``client.create_websocket()`` (which derives the auth frame from the
authenticated session). This module bridges that async socket into the
synchronous stream_factory interface the feed managers expect:

    stream_factory(on_tick=aggregator.on_tick, mode='full')
    stream.connect()
    threading.Thread(target=stream.run_forever_with_reconnect, daemon=True).start()
    stream.subscribe(payload)          # called on the engine thread
    stream.unsubscribe(payload)        # on switch/stop

A dedicated asyncio event loop runs inside the spawned daemon thread; the
engine thread schedules subscribe/unsubscribe coroutines onto it via
asyncio.run_coroutine_threadsafe.

Integration-as-SmartAPI: every SFeed message is normalized into the SAME
shared wire schema SmartTickStream/UpstoxTickStream produce (token,
last_traded_price, open_interest, volume_trade_for_the_day, closed_price,
average_traded_price) so it feeds straight into
market/quotes/tick_aggregator.py unchanged.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable


def _session_client():
    """Return the authenticated Kotak NeoAPI client (lazy import keeps the
    SDK out of module-load so public-only mode stays broker-free)."""
    from brokers.kotak.client import _session

    return _session.client


class KotakTickStream:
    """Wraps the async SFeed WebSocket into the shared stream_factory API."""

    def __init__(self, on_tick: Callable[[dict], None], mode: str = "full") -> None:
        self._on_tick = on_tick
        self._mode = mode
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any = None
        self._pending: list = []
        self._lock = threading.Lock()
        self._closed = False
        self._connected = threading.Event()

    def connect(self) -> None:
        """Credentials + socket are established lazily inside the loop thread
        (see _run). Nothing to do here on the engine thread."""
        pass

    @staticmethod
    def _build_tokens(instruments: list[dict]) -> list:
        from neo_api_client.websocket.feed.models import WsToken

        return [
            WsToken(str(i["exchange_segment"]), str(i["instrument_token"]))
            for i in instruments
        ]

    def subscribe(self, instruments: list[dict]) -> None:
        tokens = self._build_tokens(instruments)
        loop = self._loop
        ws = self._ws
        if loop is not None and ws is not None:
            fut = asyncio.run_coroutine_threadsafe(ws.subscribe_scrips(tokens), loop)
            try:
                fut.result(timeout=10)
            except Exception:  # noqa: BLE001 - a failed subscribe must not block startup
                pass
        else:
            # Socket not open yet — flush these once _run() connects.
            with self._lock:
                self._pending.extend(tokens)

    def unsubscribe(self, instruments: list[dict]) -> None:
        loop = self._loop
        ws = self._ws
        if loop is not None and ws is not None:
            tokens = self._build_tokens(instruments)
            fut = asyncio.run_coroutine_threadsafe(ws.unsubscribe_scrips(tokens), loop)
            try:
                fut.result(timeout=10)
            except Exception:  # noqa: BLE001
                pass

    def run_forever_with_reconnect(self) -> None:
        """Owns the asyncio event loop for the SFeed socket (spawned thread)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._run())
        finally:
            self._loop = None

    async def _run(self) -> None:
        from neo_api_client.websocket.feed.client import SFeedWebSocket

        client = _session_client()
        ws: SFeedWebSocket = client.create_websocket()
        async with ws:
            self._ws = ws
            self._connected.set()
            with self._lock:
                pending = self._pending
                self._pending = []
            if pending:
                try:
                    await ws.subscribe_scrips(pending)
                except Exception:  # noqa: BLE001
                    pass
            try:
                async for message in ws:
                    normalized = self._normalize(message)
                    if normalized is not None:
                        self._on_tick(normalized)
            finally:
                self._ws = None
                self._connected.clear()

    @staticmethod
    def _normalize(message: Any) -> dict | None:
        token = getattr(message, "instrument_token", None)
        if token is None:
            return None
        ltp = getattr(message, "last_traded_price", None)
        if ltp is None:
            return None
        return {
            "token": str(token),
            "last_traded_price": ltp,
            "average_traded_price": getattr(message, "average_trade_price", None),
            "closed_price": getattr(message, "close_price", None),
            "open_interest": getattr(message, "open_interest", None),
            "volume_trade_for_the_day": getattr(message, "volume_traded_today", None),
        }

    def stop(self) -> None:
        self._closed = True
        loop = self._loop
        ws = self._ws
        if loop is not None and ws is not None:
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop).result(timeout=5)
            except Exception:  # noqa: BLE001
                pass
