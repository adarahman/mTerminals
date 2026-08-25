"""Initial state delivery for newly connected dashboard clients."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aiohttp.client_exceptions import ClientConnectionResetError


class WebSocketHandshakeSender:
    """Send current runtime snapshots through injected read-only sources."""

    def __init__(
        self,
        *,
        encode: Callable[[dict], str],
        market_lock,
        market_payload: Callable[[], Any],
        baseline_version: Callable[[], Any],
        index_quotes: Callable[[], Any],
        pipeline_status: Callable[[], dict],
        funds: Callable[[], Any],
        algo_status: Callable[[], dict],
        reconciliation_alert: Callable[[], Any],
        paper_snapshot: Callable[[], tuple[dict, list]],
    ):
        self._encode = encode
        self._market_lock = market_lock
        self._market_payload = market_payload
        self._baseline_version = baseline_version
        self._index_quotes = index_quotes
        self._pipeline_status = pipeline_status
        self._funds = funds
        self._algo_status = algo_status
        self._reconciliation_alert = reconciliation_alert
        self._paper_snapshot = paper_snapshot

    async def _send(
        self,
        websocket,
        message_type: str,
        payload,
        **extra,
    ) -> bool:
        if websocket.closed:
            return False

        message = {
            "type": message_type,
            "payload": payload,
            **extra,
        }

        try:
            await websocket.send_str(self._encode(message))
            return True
        except (ClientConnectionResetError, ConnectionResetError):
            return False

    async def send(self, websocket, *, send_full: bool) -> None:
        if websocket.closed:
            return

        if send_full:
            async with self._market_lock:
                payload = self._market_payload()

                if payload is not None:
                    ok = await self._send(
                        websocket,
                        "full",
                        payload,
                        version=self._baseline_version(),
                    )
                    if not ok:
                        return

        quotes = self._index_quotes()
        if quotes:
            if not await self._send(
                websocket,
                "indexQuotes",
                quotes,
            ):
                return

        if not await self._send(
            websocket,
            "pipelineStatus",
            self._pipeline_status(),
        ):
            return

        funds = self._funds()
        if funds is not None:
            if not await self._send(
                websocket,
                "funds",
                funds,
            ):
                return

        try:
            if not await self._send(
                websocket,
                "algoStatus",
                self._algo_status(),
            ):
                return
        except Exception as exc:
            print(
                f"[algo-status] initial snapshot failed: {exc}",
                flush=True,
            )

        alert = self._reconciliation_alert()
        if alert is not None:
            try:
                if not await self._send(
                    websocket,
                    "reconciliationAlert",
                    alert,
                ):
                    return
            except Exception as exc:
                print(
                    f"[position_reconciler] initial alert snapshot failed: {exc}",
                    flush=True,
                )

        try:
            portfolio, orders = self._paper_snapshot()

            if not await self._send(
                websocket,
                "portfolio",
                portfolio,
            ):
                return

            await self._send(
                websocket,
                "orders",
                orders,
            )

        except Exception as exc:
            print(
                f"[paper-trading] initial snapshot failed: {exc}",
                flush=True,
            )